"""Tests for the DEF design-versus-router analysis (issue #1006).

The instrument runs against a synthetic mini-DEF whose answer is known by
construction, because the real 10 MB routed DEF was never available on a
dispatch worker while this was written -- the same shape the repository's
``docs/verification-rules.md`` rule 1 exists for: watch the harness fail
before trusting it.

Four verdicts are pinned:

* a ``VSS`` Metal2 shape that is a via pad, with the signal wire stopping at
  spacing distance -> ``power_via_land_spaced``;
* a signal shape that *overlaps* power geometry -> ``router_overlap_signal_crosses_power``;
* no VSS Metal2 geometry in the window -> ``undetermined``;
* the same geometry but the violation site far away -> no power shapes, no
  verdict.

And the fifth, the branch every real site in ``def_site_analysis.json``
takes and the one the docs conclusion rests on: a ``+ SHAPE DRCFILL``
landing strip -> ``fill_wire_spaced``. It was added after review because
the fixture's plain Metal2 stripe exercised only ``power_wire_spaced`` --
the verdict actually shipping for all 24 sites had no test.

And the extension form (#1013): a path point written ``( x y ext )`` records
the extension per segment end (``ext_start``/``ext_end``) and never lets it
become the width -- the old behaviour consumed the extension number as the
path width whenever the path had none of its own, silently inflating the
geometry of any pair that completed.

And applying it (#1022): the extension grows the emitted rect past its point
along the path axis, so a signal end that touches power only once extended
names the verdict -- ``router_overlap_signal_crosses_power``, not a
spaced-anything. The record also counts the extensions the parse saw, per run
and per site, so "are they all zero" is a census, never a capped sample read
as one.

And the skip-keyword arguments (#1021, one class over from #1013): the
numeric argument of a skipped keyword -- ``STYLE <n>``/``MASK <n>`` on a
path, ``WEIGHT <n>`` at net level -- is consumed with its keyword and never
reaches the width branch. The plain-widthless and ``TAPERRULE <name>`` paths
stay pinned as controls.

And the refusal path: a site whose bbox is not where any geometry lives, and
inputs that are missing, must produce ``REFUSED``/no verdict rather than a
half-answer.
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import pytest  # noqa: E402
import analyze_def_sites as ads  # noqa: E402
from classify_route_drc import parse_drc_report  # noqa: E402

#: 1000 DBU per micron keeps the fixture arithmetic legible.
UNITS = 1000

#: Geometry by construction:
#: * VSS M1 followpin (1000,1000)-(5000,1000) -- the ``*`` test;
#: * VSS Metal2 stripe x=2000, y 1000..4000 -- a *wire* origin;
#: * VSS Metal2 DRCFILL strip x=7000, y 1000..3000 -- a *fill-wire* origin,
#:   the branch every real site takes (``fill_wire_spaced``);
#: * VSS via land at (4500,1500): a placed via whose DEF definition carries an
#:   Metal2 pad (4300..4700, 1300..1700) -- a *via_pad* origin, STRIPE-tagged
#:   as pdngen's connect geometry is in the real DEF;
#: * VSS STRIPE-tagged RECT pad at (9000,1000) -- a *pad* origin;
#: * VSS via placement at (3000,3000) whose definition is NOT in the DEF --
#:   *unresolved*, counted;
#: * signal wires near the pad (no overlap), crossing the stripe (overlap),
#:   and far from everything.
DEF = f"""VERSION 5.8 ;
DIVIDERCHAR "/" ;
BUSBITCHARS "[]" ;
DESIGN ladder_dp ;
UNITS DISTANCE MICRONS {UNITS} ;
VIAS 1 ;
  - via_m2_land
    + RECT Metal1 ( -140 -140 ) ( 140 140 )
    + RECT Metal2 ( -200 -200 ) ( 200 200 )
    + RECT Metal3 ( -140 -140 ) ( 140 140 ) ;
END VIAS
SPECIALNETS 1 ;
  - VSS ( * )
    + ROUTED Metal1 2000 + SHAPE FOLLOWPIN ( 1000 1000 ) ( 5000 * )
    + NEW Metal2 400 ( 2000 1000 ) ( * 4000 )
    + NEW Metal2 400 + SHAPE DRCFILL ( 7000 1000 ) ( * 3000 )
    + NEW Metal2 400 + SHAPE STRIPE ( 4500 1500 ) via_m2_land
    + NEW Metal2 400 + SHAPE STRIPE ( 9000 1000 ) RECT ( -100 -100 ) ( 100 100 )
    + NEW Metal2 400 ( 3000 3000 ) via_not_in_def
    ;
END SPECIALNETS
NETS 3 ;
  - _00146_ ( U1 A ) ( U2 A )
    + ROUTED Metal2 400 ( 4300 2050 ) ( 4600 2050 )
    ;
  - _00306_ ( U3 A ) ( U4 A )
    + ROUTED Metal2 400 ( 1900 2500 ) ( 2100 2500 )
    ;
  - _00426_ ( U5 A ) ( U6 A )
    + ROUTED Metal2 400 ( 6500 4000 ) ( 6800 4000 )
      NEW Metal2 ( 6800 4000 ) Via2_VH
      NEW Metal2 ( 6800 4000 ) RECT ( -280 -660 280 0 )
    ;
END NETS
END DESIGN
"""

#: Four distinct violation sites, each exercising one verdict by construction.
SITE_PAD = "(4.3000, 1.8000) - (4.5000, 2.0000)"  # abuts the via pad (gap 100)
SITE_WIRE = "(2.2000, 2.0000) - (2.6000, 2.6000)"  # 200 dbu from the M2 stripe
SITE_OVERLAP = "(1.9000, 2.4000) - (2.1000, 2.6000)"  # wire crosses the stripe
SITE_FAR = "(40.0000, 40.0000) - (40.2000, 40.2000)"  # nothing within 1 um
SITE_FILL = "(7.3000, 2.0000) - (7.5000, 2.4000)"  # 100 dbu from the DRCFILL strip
SITE_POWER_PAD = "(9.0000, 0.0000) - (9.2000, 0.2000)"  # 700 dbu from the RECT pad


def _drc(bboxes_and_srcs: list[tuple[str, str]]) -> dict:
    report = "".join(
        f"violation type: Metal Spacing\n\tsrcs: {srcs}\n\tbbox = {bbox}\n\tLayer: Metal2\n"
        for bbox, srcs in bboxes_and_srcs
    )
    return parse_drc_report(report)


def _analyse(bboxes_and_srcs: list[tuple[str, str]], window_um: float = 1.0) -> list[dict]:
    geometry = ads.DefGeometry(DEF)
    return ads.analyse(geometry, _drc(bboxes_and_srcs), frozenset({"VSS", "VDD"}), window_um)


def _site(verdict: str, records: list[dict]) -> dict:
    matches = [r for r in records if r["verdict"] == verdict]
    assert matches, [(r["bbox"], r["verdict"]) for r in records]
    return matches[0]


def test_a_via_pad_at_spacing_is_power_via_land() -> None:
    """The M2 via land at (4.3-4.7, 1.3-1.7) bounds the site from below."""
    records = _analyse([(SITE_PAD, "_00146_ VSS")])
    record = _site("power_via_land_spaced", records)
    nearest = record["power_shapes_in_window"][0]
    assert nearest["origin"] == "via_pad" and nearest["gap_dbu"] == 100, nearest
    assert nearest["shape"] == "STRIPE", nearest
    assert record["signal_overlaps_power"] is False


def test_wire_geometry_reads_as_wire_origin() -> None:
    """The M2 stripe centreline, widened to its metal, touches the site."""
    records = _analyse([(SITE_WIRE, "_00426_ VSS")])
    record = _site("power_wire_spaced", records)
    nearest = record["power_shapes_in_window"][0]
    # stripe centreline x=2000 at width 400 -> metal edge x=2200 = bbox x1
    assert nearest["origin"] == "wire" and nearest["gap_dbu"] == 0, nearest


def test_a_drcfill_strip_is_fill_wire_spaced() -> None:
    """The verdict every real site takes: nearest power shape is a DRCFILL wire.

    The strip centreline x=7000 at width 400 -> metal edge x=7200; the bbox
    starts at 7300, so the gap is 100 -- and the DRCFILL tag, not the wire
    origin, is what names the verdict.
    """
    records = _analyse([(SITE_FILL, "_00146_ VSS")])
    record = _site("fill_wire_spaced", records)
    nearest = record["power_shapes_in_window"][0]
    assert nearest["origin"] == "wire", nearest
    assert nearest["shape"] == "DRCFILL", nearest
    assert nearest["gap_dbu"] == 100, nearest


def test_pad_and_via_pad_entries_carry_their_shape_tag() -> None:
    """``pad``/``via_pad`` entries carry the SHAPE tag, like wire entries do.

    The docs' "STRIPE-tagged via pads" sentence must be readable from the
    record itself, not only from prose (#1011).
    """
    records = _analyse([(SITE_PAD, "_00146_ VSS"), (SITE_POWER_PAD, "_00146_ VSS")])
    via_site = [r for r in records if r["bbox"] == SITE_PAD][0]
    nearest = via_site["power_shapes_in_window"][0]
    assert nearest["origin"] == "via_pad" and nearest["shape"] == "STRIPE", nearest

    pad_site = [r for r in records if r["bbox"] == SITE_POWER_PAD][0]
    assert pad_site["verdict"] == "power_via_land_spaced", pad_site
    nearest = pad_site["power_shapes_in_window"][0]
    assert nearest["origin"] == "pad" and nearest["shape"] == "STRIPE", nearest
    assert nearest["gap_dbu"] == 700, nearest


def test_power_shapes_in_window_total_is_not_truncated() -> None:
    """``power_shapes_in_window_total`` counts the window, not the 8-entry cap.

    The listing stays truncated at 8 entries; the total beside it is what
    lets a reader know a per-site minimum is exact without the re-run (#1011).
    """
    stripes = "\n".join(
        f"    + NEW Metal2 100 ( {x} 1000 ) ( * 4000 )" for x in range(1400, 3400, 200)
    )
    dense = DEF.replace(
        "    + NEW Metal2 400 ( 2000 1000 ) ( * 4000 )\n", ""
    ).replace("    ;\nEND SPECIALNETS", f"{stripes}\n    ;\nEND SPECIALNETS")
    geometry = ads.DefGeometry(dense)
    records = ads.analyse(
        geometry, _drc([(SITE_WIRE, "_00426_ VSS")]), frozenset({"VSS", "VDD"}), 1.0
    )
    record = records[0]
    total = record["power_shapes_in_window_total"]
    assert total > 8, (total, record["power_shapes_in_window"])
    assert len(record["power_shapes_in_window"]) == 8
    # the listing is the total's nearest-8: sorted ascending, minima intact
    listed = [p["gap_dbu"] for p in record["power_shapes_in_window"]]
    assert listed == sorted(listed)


def test_the_record_names_the_overlap_lower_bound(tmp_path: pathlib.Path) -> None:
    """The record's own ``what`` states the signal_overlaps_power limitation.

    Regular NETS carry no width, so signal wires are zero-width centrelines
    and an overlap needs the centreline itself to cross -- a lower bound,
    stated in the record so it travels with the evidence (#1011).
    """
    def_path = tmp_path / "x.def"
    def_path.write_text(DEF)
    drc_path = tmp_path / "x.rpt"
    drc_path.write_text(
        "violation type: Metal Spacing\n\tsrcs: _00146_ VSS\n"
        f"\tbbox = {SITE_PAD}\n\tLayer: Metal2\n"
    )
    par_path = tmp_path / "par.json"
    par_path.write_text('{"power": {"power_net": "VDD", "ground_net": "VSS"}}')
    out_path = tmp_path / "out.json"
    rc = ads.main(
        [
            "--def", str(def_path),
            "--drc-report", str(drc_path),
            "--par-response", str(par_path),
            "--output", str(out_path),
            "--source-job", "test",
        ]
    )
    assert rc == ads.EXIT_OK, rc
    record = json.loads(out_path.read_text())
    assert "lower bound" in record["what"], record["what"]
    assert "zero-width centrelines" in record["what"], record["what"]
    site = record["sites"][0]
    assert site["power_shapes_in_window_total"] == len(site["power_shapes_in_window"])
    pads = [p for p in site["power_shapes_in_window"] if p["origin"] in ("pad", "via_pad")]
    assert pads and all("shape" in p for p in pads), pads


def test_an_overlap_is_reported_as_router_overlap() -> None:
    """A signal wire crossing the VSS stripe must be named as such."""
    records = _analyse([(SITE_OVERLAP, "_00306_ VSS")])
    record = _site("router_overlap_signal_crosses_power", records)
    assert record["signal_overlaps_power"] is True


def test_no_vss_geometry_in_window_is_undetermined() -> None:
    """A site far from any VSS Metal2 geometry gets no verdict, not a guess."""
    records = _analyse([(SITE_FAR, "_00146_ VSS")])
    record = _site("undetermined", records)
    assert not record["power_shapes_in_window"]


def test_an_unresolved_via_is_counted_with_its_name() -> None:
    """A via whose definition is not in the DEF is counted, never dropped."""
    geometry = ads.DefGeometry(DEF)
    found = geometry.shapes_near(
        "VSS", "Metal2", (2500, 2500, 3500, 3500), "specialnets"
    )
    assert not found["vias_resolved"], found["vias_resolved"]
    assert [v["via"] for v in found["vias_unresolved"]] == ["via_not_in_def"]


def test_a_resolved_via_yields_its_definiton_pad() -> None:
    """The via land's Metal2 pad comes from the DEF VIAS section, exactly."""
    geometry = ads.DefGeometry(DEF)
    found = geometry.shapes_near(
        "VSS", "Metal2", (4000, 1000, 5000, 2000), "specialnets"
    )
    assert [v["rect"] for v in found["vias_resolved"]] == [[4300, 1300, 4700, 1700]]
    assert not found["vias_unresolved"]


def test_a_star_coordinate_resolves_against_the_previous_point() -> None:
    """``( 5000 * )`` must close the followpin at y=1000, widened to its metal."""
    geometry = ads.DefGeometry(DEF)
    found = geometry.shapes_near("VSS", "Metal1", (0, 0, 8000, 8000), "specialnets")
    # centreline y=1000 at width 2000 -> metal spans y 0..2000
    assert any(
        (w["x1"], w["y1"], w["x2"], w["y2"]) == (1000, 0, 5000, 2000)
        for w in found["wires"]
    ), found["wires"]


def test_a_viarule_via_yields_derived_pads_per_layer() -> None:
    """CUTSIZE 520 + ENCLOSURE 20 120 120 20 -> Metal2 560x760, Metal3 760x560."""
    geometry = ads.DefGeometry(DEF)
    at = geometry.shapes_near(
        "VSS", "Metal2", (4000, 1000, 5000, 2000), "specialnets"
    )
    assert [v["rect"] for v in at["vias_resolved"]] == [[4300, 1300, 4700, 1700]]  # explicit-RECT via
    viarule = ads.DefGeometry(
        DEF.replace(
            "END VIAS",
            """  - via_viarule
    + VIARULE Via2_GEN_HH + CUTSIZE 520 520 + LAYERS Metal2 Via2 Metal3
    + CUTSPACING -264 -264 + ENCLOSURE 20 120 120 20 ;
END VIAS""",
        ).replace("( 4500 1500 ) via_m2_land", "( 4500 1500 ) via_viarule")
    )
    pads = viarule.via_pads({"via": "via_viarule", "x": 4500, "y": 1500}, "Metal2")
    assert pads == [(4220, 1120, 4780, 1880)], pads
    pads_m3 = viarule.via_pads({"via": "via_viarule", "x": 4500, "y": 1500}, "Metal3")
    assert pads_m3 == [(4120, 1220, 4880, 1780)], pads_m3


def test_the_def_parser_counts_what_it_does_not_understand() -> None:
    """A token outside the grammar is counted, never silently dropped.

    An identifier after a point is a via by DEF grammar, so the injected
    junk here is a bare number where no number is legal -- a second width
    on one path.
    """
    text = DEF.replace("( 6500 4000 )", "( 6500 4000 ) 17")
    geometry = ads.DefGeometry(text)
    assert geometry.unparsed_tokens >= 1, geometry.unparsed_tokens


def test_the_quad_rect_form_is_a_pad_on_the_signal_net() -> None:
    """OpenROAD's one-paren RECT ( dx1 dy1 dx2 dy2 ) is a via pad, parsed."""
    geometry = ads.DefGeometry(DEF)
    found = geometry.shapes_near("_00426_", "Metal2", (6000, 3000, 7500, 4500), "nets")
    rects = [(p["x1"], p["y1"], p["x2"], p["y2"]) for p in found["pads"]]
    assert (6520, 3340, 7080, 4000) in rects, rects


def test_an_extension_point_completing_a_pair_never_becomes_the_width() -> None:
    """``( x y ext )``: the extension is recorded, never consumed as width.

    The input that defeats the old behaviour: on current main the widthless
    path's extension number fell through to the ``width is None`` branch, the
    garbled tokens counted as unparsed, and the completing pair emitted a
    metal rect inflated to half the extension value -- the silent wrong
    geometry field the issue names.
    """
    text = DEF.replace(
        "NEW Metal2 ( 6800 4000 ) Via2_VH",
        "NEW Metal2 ( 4300 2050 ) ( * 2300 0 ) ( 4600 * )",
    )
    geometry = ads.DefGeometry(text)
    assert geometry.unparsed_tokens == 0, geometry.unparsed_tokens
    found = geometry.shapes_near("_00426_", "Metal2", (0, 0, 8000, 8000), "nets")
    wires = [
        (w["x1"], w["y1"], w["x2"], w["y2"], w["width"], w["ext_start"], w["ext_end"])
        for w in found["wires"]
    ]
    # the ext-point completed the pair: centreline bbox at width None (no
    # widening -- half=0), the extension riding alongside as ext_end -- never
    # as width
    assert (4300, 2050, 4300, 2300, None, None, 0) in wires, wires
    # and the plain pair behind it is unchanged
    assert (4300, 2300, 4600, 2300, None, 0, None) in wires, wires


def test_extension_points_record_both_ends_of_the_pair() -> None:
    """ext on the path's first point lands as ``ext_start``; on the last, ``ext_end``."""
    text = DEF.replace(
        "+ ROUTED Metal2 400 ( 4300 2050 ) ( 4600 2050 )",
        "+ ROUTED Metal2 400 ( 4300 2050 25 ) ( 4600 * ) ( * 2300 0 )",
    )
    geometry = ads.DefGeometry(text)
    assert geometry.unparsed_tokens == 0, geometry.unparsed_tokens
    found = geometry.shapes_near("_00146_", "Metal2", (0, 0, 8000, 8000), "nets")
    segs = sorted(
        (w["x1"], w["y1"], w["x2"], w["y2"], w["width"], w["ext_start"], w["ext_end"])
        for w in found["wires"]
    )
    # width 400 widens each centreline pair by half on the perpendicular
    # axis, and ext_start=25 extends the first segment's start end 25 DBU
    # past its point (#1022)
    assert (4275, 1850, 4600, 2250, 400, 25, None) in segs, segs
    assert (4400, 2050, 4800, 2300, 400, None, 0) in segs, segs


#: The extension-touch fixture (#1022): a VSS Metal2 stripe whose metal is
#: x 1800..2200, and a signal wire whose centreline stops 200 DBU short of it
#: at ( 2400 2500 ) but carries ``ext 200`` on that end -- the real metal
#: reaches x 2200 and touches the stripe only once the extension is applied.
EXT_TOUCH_DEF = """VERSION 5.8 ;
UNITS DISTANCE MICRONS 1000 ;
SPECIALNETS 1 ;
  - VSS ( * )
    + NEW Metal2 400 ( 2000 1000 ) ( * 4000 )
    ;
END SPECIALNETS
NETS 1 ;
  - sig ( U1 A ) ( U2 A )
    + ROUTED Metal2 400 ( 2500 2500 ) ( 2400 * 200 )
    ;
END NETS
END DESIGN
"""

SITE_EXT_TOUCH = "(2.3000, 2.4000) - (2.5000, 2.6000)"


def _analyse_ext_touch(geometry: ads.DefGeometry) -> dict:
    records = ads.analyse(
        geometry, _drc([(SITE_EXT_TOUCH, "sig VSS")]), frozenset({"VSS", "VDD"}), 1.0
    )
    assert len(records) == 1, [(r["bbox"], r["verdict"]) for r in records]
    return records[0]


def test_an_extended_end_touches_power_only_once_extended() -> None:
    """The red-first test for #1022: ext at the end point extends the rect.

    The centreline pair ends at ( 2400 2500 ), 200 DBU clear of the stripe
    metal -- on the #1017 head, where the extension is recorded but never
    applied, that is no overlap and verdict ``power_wire_spaced``. With the
    extension applied the metal reaches x 2200, touches the stripe, and the
    verdict flips to ``router_overlap_signal_crosses_power``. This test was
    run on the #1017 head first and failed exactly so.
    """
    record = _analyse_ext_touch(ads.DefGeometry(EXT_TOUCH_DEF))
    assert record["signal_overlaps_power"] is True, record
    assert record["verdict"] == "router_overlap_signal_crosses_power", record


def test_an_extended_start_end_touches_power_on_a_vertical_path() -> None:
    """``ext_start`` on a vertical path extends the y-min side, ends independent.

    The VSS wire is horizontal, metal y 2800..3200; the signal pair starts at
    ( 2500 3300 ) -- 100 DBU clear -- carrying ``ext 100`` on that start
    point. The extension closes exactly that gap.
    """
    text = EXT_TOUCH_DEF.replace(
        "+ NEW Metal2 400 ( 2000 1000 ) ( * 4000 )",
        "+ NEW Metal2 400 ( 1000 3000 ) ( 4000 3000 )",
    ).replace(
        "+ ROUTED Metal2 400 ( 2500 2500 ) ( 2400 * 200 )",
        "+ ROUTED Metal2 400 ( 2500 3300 100 ) ( * 3800 )",
    )
    record = _analyse_ext_touch(ads.DefGeometry(text))
    assert record["signal_overlaps_power"] is True, record
    assert record["verdict"] == "router_overlap_signal_crosses_power", record


def test_extension_grows_the_diagonal_bbox_per_end_on_both_axes() -> None:
    """A non-Manhattan pair keeps its bbox fallback, grown by each end's own ext.

    A diagonal has no single path axis to extend along, so each point's
    extension is applied on both axes at the sides of the bbox that point
    owns -- the fallback stays a conservative cover, never borrowing one
    end's extension for the other.
    """
    text = EXT_TOUCH_DEF.replace(
        "+ ROUTED Metal2 400 ( 2500 2500 ) ( 2400 * 200 )",
        "+ ROUTED Metal2 400 ( 2500 2500 ) ( 2600 2600 300 )",
    )
    geometry = ads.DefGeometry(text)
    found = geometry.shapes_near("sig", "Metal2", (0, 0, 8000, 8000), "nets")
    assert [(w["x1"], w["y1"], w["x2"], w["y2"]) for w in found["wires"]] == [
        (2500, 2500, 2900, 2900)
    ], found["wires"]


#: The census fixture: three extension points across both net sections, one
#: of them zero -- total 3, non-zero 2.
CENSUS_DEF = """VERSION 5.8 ;
UNITS DISTANCE MICRONS 1000 ;
SPECIALNETS 1 ;
  - VSS ( * )
    + NEW Metal2 400 ( 2000 1000 50 ) ( * 4000 )
    ;
END SPECIALNETS
NETS 1 ;
  - sig ( U1 A ) ( U2 A )
    + ROUTED Metal2 400 ( 2500 2500 ) ( 2400 * 200 )
      NEW Metal2 400 ( 3000 3000 ) ( 3200 * 0 )
    ;
END NETS
END DESIGN
"""


def test_the_parse_census_counts_extension_points_per_run() -> None:
    """Total ext points seen and non-zero ones, across both net sections.

    #1022's census: the committed record's capped ``unparsed_samples`` made
    "the extensions are all 0" *plausible* -- a sample read as a census. The
    parse now counts, so the question has a number.
    """
    geometry = ads.DefGeometry(CENSUS_DEF)
    assert geometry.ext_points_total == 3, geometry.ext_points_total
    assert geometry.ext_points_nonzero == 2, geometry.ext_points_nonzero


def _points(body: str) -> tuple[int, int]:
    text = (
        "VERSION 5.8 ;\nUNITS DISTANCE MICRONS 1000 ;\nNETS 1 ;\n"
        f"  - sig ( U1 A )\n    {body}\n    ;\nEND NETS\nEND DESIGN\n"
    )
    g = ads.DefGeometry(text)
    assert g.unparsed_tokens == 0, g.unparsed_samples
    return g.ext_points_total, g.ext_points_nonzero


def test_the_census_counts_points_not_segment_ends() -> None:
    """One ext point is one point however the path shapes around it.

    Review of #1025: counting ends of completed wires reported 2 for an
    interior point (A), and 0 for a via-terminated stub (D) and for a point
    before ``NEW`` (F) -- the latter silently, with zero unparsed tokens.
    """
    # A: ext only on the middle point of a 3-point path
    assert _points("+ ROUTED Metal2 400 ( 1000 3000 ) ( 2000 3000 60 ) ( 3000 3000 )") == (1, 1)
    # D: single point with ext terminated by a via
    assert _points("+ ROUTED Metal2 400 ( 2000 3000 70 ) via2_1") == (1, 1)
    # F: ext point, then a new path start
    assert _points("+ ROUTED Metal2 400 ( 2000 3000 80 ) NEW Metal2 400 ( 1 1 ) ( 5 1 )") == (1, 1)
    # zero ext is counted as seen, not non-zero
    assert _points("+ ROUTED Metal2 400 ( 2000 3000 0 ) via2_1") == (1, 0)


def test_the_site_record_counts_ext_ends_in_its_window() -> None:
    """Per-site census: extension ends among the window's wires, non-zero apart."""
    record = _analyse_ext_touch(ads.DefGeometry(EXT_TOUCH_DEF))
    assert record["wire_ext_ends_in_window"] == {"total": 1, "nonzero": 1}, record


def test_the_census_reaches_the_json_record(tmp_path: pathlib.Path) -> None:
    """``def_site_analysis.json`` carries the census and the per-site counts."""
    def_path = tmp_path / "x.def"
    def_path.write_text(EXT_TOUCH_DEF)
    drc_path = tmp_path / "x.rpt"
    drc_path.write_text(
        "violation type: Metal Spacing\n\tsrcs: sig VSS\n"
        f"\tbbox = {SITE_EXT_TOUCH}\n\tLayer: Metal2\n"
    )
    par_path = tmp_path / "par.json"
    par_path.write_text('{"power": {"power_net": "VDD", "ground_net": "VSS"}}')
    out_path = tmp_path / "out.json"
    rc = ads.main(
        [
            "--def", str(def_path),
            "--drc-report", str(drc_path),
            "--par-response", str(par_path),
            "--output", str(out_path),
        ]
    )
    assert rc == ads.EXIT_OK, rc
    result = json.loads(out_path.read_text())
    assert result["def_parse"]["extension_points_total"] == 1, result["def_parse"]
    assert result["def_parse"]["extension_points_nonzero"] == 1, result["def_parse"]
    assert result["sites"][0]["wire_ext_ends_in_window"] == {"total": 1, "nonzero": 1}
    assert result["sites"][0]["verdict"] == "router_overlap_signal_crosses_power"


def test_both_ends_of_one_pair_extend_independently() -> None:
    """``ext_start`` grows the start side and ``ext_end`` the end side, one pair.

    The pair runs x 2500..3500 at y 3000; ext 75 on the start point and 100
    on the end point leave the rect at x 2425..3600 -- each end its own.
    """
    text = EXT_TOUCH_DEF.replace(
        "+ ROUTED Metal2 400 ( 2500 2500 ) ( 2400 * 200 )",
        "+ ROUTED Metal2 400 ( 2500 3000 75 ) ( 3500 * 100 )",
    )
    geometry = ads.DefGeometry(text)
    found = geometry.shapes_near("sig", "Metal2", (0, 0, 8000, 8000), "nets")
    assert [(w["x1"], w["y1"], w["x2"], w["y2"]) for w in found["wires"]] == [
        (2425, 2800, 3600, 3200)
    ], found["wires"]


def test_the_control_zeroing_ext_flips_the_overlap_verdict(monkeypatch) -> None:
    """Injected-bug control (#1022): ignoring ext must turn the red-first test red.

    The injection strips every ``( x y ext )`` form down to ``( x y )`` inside
    ``DefGeometry.__init__`` -- the parser then never sees an extension and
    builds exactly the pre-fix rects. It must act on the *input text*, not on
    the parsed shape dicts: the rect is built at parse time, so zeroing
    ``ext_start``/``ext_end`` afterwards changes nothing and controls nothing.
    Under the injection the same fixture must come back non-overlapping; if
    it did not, the overlap test above would not be testing the extension at
    all.
    """
    import re

    original_init = ads.DefGeometry.__init__
    ext_form = re.compile(r"\(\s*(-?\d+|\*)\s+(-?\d+|\*)\s+-?\d+\s*\)")

    def init_with_ext_ignored(self: ads.DefGeometry, text: str) -> None:
        original_init(self, ext_form.sub(lambda m: f"( {m.group(1)} {m.group(2)} )", text))

    monkeypatch.setattr(ads.DefGeometry, "__init__", init_with_ext_ignored)
    record = _analyse_ext_touch(ads.DefGeometry(EXT_TOUCH_DEF))
    assert record["signal_overlaps_power"] is False, record
    assert record["verdict"] == "power_wire_spaced", record


#: The ``clk`` NDR path from the routed DEF, in the form the committed
#: ``def_site_analysis.json`` counted 6 unparsed tokens for (4 from the
#: extension form, 2 for the non-default rule). The full 10 MB DEF stays on
#: the #1010 review host; this excerpt carries the extension line verbatim.
CLK_EXCERPT = """VERSION 5.8 ;
UNITS DISTANCE MICRONS 2000 ;
NETS 1 ;
  - clk ( U1 CK ) ( U2 CK )
    + NONDEFAULTRULE CTS_NDR_0
    + NEW Metal4 ( 276640 799680 ) ( * 975600 0 )
    ;
END NETS
END DESIGN
"""


def test_the_clk_ndr_path_excerpt_parses_its_extension_point() -> None:
    """The real ``clk`` path form: unparsed 6 -> 2, the pair completes widthless.

    On current main the extension number ``975600`` was consumed as the path
    width and the four surrounding tokens counted unparsed, so the segment
    was dropped before its pair could complete.
    """
    geometry = ads.DefGeometry(CLK_EXCERPT)
    assert geometry.unparsed_tokens == 2, geometry.unparsed_tokens
    tokens = sorted(s["token"] for s in geometry.unparsed_samples)
    assert tokens == ["CTS_NDR_0", "NONDEFAULTRULE"], tokens
    wires = [
        (w["x1"], w["y1"], w["x2"], w["y2"], w["width"], w["ext_start"], w["ext_end"])
        for w in geometry.nets["clk"]
    ]
    assert wires == [(276640, 799680, 276640, 975600, None, None, 0)], wires


def test_a_nondefaultrule_stays_counted_never_geometry() -> None:
    """``+ NONDEFAULTRULE <name>`` names a routing rule, not geometry.

    It stays counted as unparsed -- the 4 such tokens in the committed
    record -- and must never become a width, a point, or a shape.
    """
    text = DEF.replace(
        "  - _00146_ ( U1 A ) ( U2 A )\n",
        "  - _00146_ ( U1 A ) ( U2 A )\n    + NONDEFAULTRULE CTS_NDR_0\n",
    )
    geometry = ads.DefGeometry(text)
    assert geometry.unparsed_tokens == 2, geometry.unparsed_tokens
    tokens = sorted(s["token"] for s in geometry.unparsed_samples)
    assert tokens == ["CTS_NDR_0", "NONDEFAULTRULE"], tokens
    found = geometry.shapes_near("_00146_", "Metal2", (4000, 1800, 4800, 2300), "nets")
    assert found["wires"], "the path behind the rule must still parse"


def _post_point_net(tail: str) -> "ads.DefGeometry":
    """A one-net DEF whose path carries ``tail`` after its points."""
    return ads.DefGeometry(
        "VERSION 5.8 ;\nUNITS DISTANCE MICRONS 1000 ;\nNETS 1 ;\n"
        "  - n ( U1 A ) ( U2 A )\n"
        "    + ROUTED Metal2 ( 1000 1000 ) ( * 2000 )\n"
        f"    {tail}\n"
        "    ;\nEND NETS\nEND DESIGN\n"
    )


def _vias(geometry: "ads.DefGeometry") -> list:
    return [s["via"] for s in geometry.nets["n"] if s["kind"] == "via"]


def test_a_plus_keyword_after_a_point_never_becomes_a_via() -> None:
    """``+ NONDEFAULTRULE ndr1`` after the points: no phantom via (#1034).

    ``+`` was skipped without ending the via-name position, so the next
    keyword was read as a via name. A via name never follows ``+`` in DEF.
    The keyword stays counted unparsed, exactly like the pre-point spelling.
    """
    geometry = _post_point_net("+ NONDEFAULTRULE ndr1")
    assert _vias(geometry) == [], geometry.nets["n"]
    assert geometry.unparsed_tokens == 2, geometry.unparsed_samples


def test_another_plus_keyword_after_a_point_never_becomes_a_via() -> None:
    """The class, not one spelling: ``+ ESTCAP`` takes the same path."""
    assert _vias(_post_point_net("+ ESTCAP 1.5")) == []


def test_a_real_via_directly_after_a_point_still_emits() -> None:
    """Control: ``( x y ) via12`` (no ``+``) is still a via shape."""
    assert _vias(_post_point_net("( 2000 2000 ) via12")) == ["via12"]


def _layerless_net(head: str) -> "ads.DefGeometry":
    """A one-net DEF whose path keyword is followed by ``head`` then Metal2."""
    return ads.DefGeometry(
        "VERSION 5.8 ;\nUNITS DISTANCE MICRONS 1000 ;\nNETS 1 ;\n"
        "  - n ( U1 A ) ( U2 A )\n"
        f"    {head} Metal2 ( 1000 1000 ) ( * 2000 )\n"
        "    ;\nEND NETS\nEND DESIGN\n"
    )


def _wire_layers(geometry: "ads.DefGeometry") -> list:
    return [s["layer"] for s in geometry.nets["n"] if s["kind"] == "wire"]


def test_a_plus_after_a_path_keyword_never_lends_its_keyword_as_the_layer() -> None:
    """``+ ROUTED + NONDEFAULTRULE ndr1 Metal2``: no wire on NONDEFAULTRULE (#1037).

    DEF 5.8 ``regularWiring`` is ``{+ COVER|+ FIXED|+ ROUTED|+ NOSHIELD}
    layerName``, so a ``+`` there is not legal -- but the walk must not turn
    the keyword after it into a layer name. ``expect_layer`` survived the
    ``+`` and the first bare word took it, so the wire landed on
    ``NONDEFAULTRULE``. Sibling of the ``expect_via`` hole in #1034. The
    input is illegal DEF, so the layer after it is deliberately not claimed
    as recovered -- only the mis-attribution is asserted away.
    """
    geometry = _layerless_net("+ ROUTED + NONDEFAULTRULE ndr1")
    assert "NONDEFAULTRULE" not in _wire_layers(geometry), geometry.nets["n"]


def test_a_plus_after_new_never_lends_its_keyword_as_the_layer() -> None:
    """The ``NEW`` spelling of the same hole."""
    geometry = ads.DefGeometry(
        "VERSION 5.8 ;\nUNITS DISTANCE MICRONS 1000 ;\nNETS 1 ;\n"
        "  - n ( U1 A ) ( U2 A )\n"
        "    + ROUTED Metal1 ( 0 0 ) ( * 500 )\n"
        "    NEW + NONDEFAULTRULE ndr1 Metal2 ( 1000 1000 ) ( * 2000 )\n"
        "    ;\nEND NETS\nEND DESIGN\n"
    )
    assert "NONDEFAULTRULE" not in _wire_layers(geometry), geometry.nets["n"]


def test_a_layer_directly_after_a_path_keyword_still_parses() -> None:
    """Control: the legal ``+ ROUTED Metal2`` is a Metal2 wire."""
    assert set(_wire_layers(_layerless_net("+ ROUTED"))) == {"Metal2"}


def test_a_style_number_on_a_widthless_path_never_becomes_the_width() -> None:
    """``STYLE <n>`` on a widthless path: argument consumed with its keyword.

    The input that defeats the old behaviour: ``STYLE`` sits in
    ``_SKIP_TOKENS``, so the keyword was skipped but its numeric argument
    fell through to the ``width is None`` branch -- width 3, a rect widened
    by half of it, and ``unparsed`` still 0 (the silent wrong value the
    issue exists to remove).
    """
    text = DEF.replace(
        "+ ROUTED Metal2 400 ( 4300 2050 ) ( 4600 2050 )",
        "+ ROUTED Metal2 STYLE 3 ( 4300 2050 ) ( * 2300 )",
    )
    geometry = ads.DefGeometry(text)
    assert geometry.unparsed_tokens == 0, geometry.unparsed_tokens
    wires = [
        (w["x1"], w["y1"], w["x2"], w["y2"], w["width"])
        for w in geometry.nets["_00146_"]
    ]
    assert wires == [(4300, 2050, 4300, 2300, None)], wires


def test_a_mask_number_on_a_widthless_path_never_becomes_the_width() -> None:
    """``MASK <n>`` on a widthless path: same class, same consumption."""
    text = DEF.replace(
        "+ ROUTED Metal2 400 ( 4300 2050 ) ( 4600 2050 )",
        "+ ROUTED Metal2 MASK 2 ( 4300 2050 ) ( * 2300 )",
    )
    geometry = ads.DefGeometry(text)
    assert geometry.unparsed_tokens == 0, geometry.unparsed_tokens
    wires = [
        (w["x1"], w["y1"], w["x2"], w["y2"], w["width"])
        for w in geometry.nets["_00146_"]
    ]
    assert wires == [(4300, 2050, 4300, 2300, None)], wires


def test_a_style_number_before_the_layer_is_also_consumed() -> None:
    """The DEF 5.8 position, ``STYLE`` before the layer name, is covered too.

    There the argument cannot reach the width branch (no layer yet), but on
    the old behaviour it landed in ``unparsed`` -- a grammar-legal path
    mislabelled as junk. Consuming it with the keyword keeps the unparsed
    count meaning "shapes the grammar missed".
    """
    text = DEF.replace(
        "+ ROUTED Metal2 400 ( 4300 2050 ) ( 4600 2050 )",
        "+ ROUTED STYLE 3 Metal2 ( 4300 2050 ) ( * 2300 )",
    )
    geometry = ads.DefGeometry(text)
    assert geometry.unparsed_tokens == 0, geometry.unparsed_tokens
    wires = [
        (w["x1"], w["y1"], w["x2"], w["y2"], w["width"])
        for w in geometry.nets["_00146_"]
    ]
    assert wires == [(4300, 2050, 4300, 2300, None)], wires


def test_skip_keyword_numbers_never_touch_a_path_that_has_a_width() -> None:
    """An explicit width followed by ``STYLE <n>``/``MASK <n>`` stays the width.

    Neither the keyword's number may overwrite the width nor be double-counted
    as unparsed noise.
    """
    text = DEF.replace(
        "+ ROUTED Metal2 400 ( 4300 2050 ) ( 4600 2050 )",
        "+ ROUTED Metal2 400 STYLE 3 ( 4300 2050 ) ( 4600 * )",
    ).replace(
        "+ ROUTED Metal2 400 ( 1900 2500 ) ( 2100 2500 )",
        "+ ROUTED Metal2 400 MASK 2 ( 1900 2500 ) ( 2100 * )",
    )
    geometry = ads.DefGeometry(text)
    assert geometry.unparsed_tokens == 0, geometry.unparsed_tokens
    for net, x1, x2 in (("_00146_", 4300, 4600), ("_00306_", 1900, 2100)):
        wires = [
            (w["x1"], w["y1"], w["x2"], w["y2"], w["width"])
            for w in geometry.nets[net]
        ]
        # centreline widened by half of the explicit 400, unmodified
        assert wires == [(x1, 2300 if net == "_00306_" else 1850, x2,
                          2700 if net == "_00306_" else 2250, 400)], wires


def test_a_weight_number_is_consumed_with_its_keyword() -> None:
    """``+ WEIGHT <n>`` at net level: the number rides with its keyword.

    The DEF grammar gives ``WEIGHT`` a numeric argument; on the old
    behaviour the keyword was skipped and the number counted unparsed at
    net level -- visible, but the same defect class, and noise in a count
    that should mean "shapes the grammar missed".
    """
    text = DEF.replace(
        "  - _00146_ ( U1 A ) ( U2 A )\n",
        "  - _00146_ ( U1 A ) ( U2 A )\n    + WEIGHT 5\n",
    )
    geometry = ads.DefGeometry(text)
    assert geometry.unparsed_tokens == 0, geometry.unparsed_tokens
    wires = [
        (w["x1"], w["y1"], w["x2"], w["y2"], w["width"])
        for w in geometry.nets["_00146_"]
    ]
    assert wires == [(4300, 1850, 4600, 2250, 400)], wires


def test_a_plain_widthless_path_is_unchanged() -> None:
    """Control: no skip-keyword at all -- widthless stays widthless, counted 0."""
    text = DEF.replace(
        "+ ROUTED Metal2 400 ( 4300 2050 ) ( 4600 2050 )",
        "+ ROUTED Metal2 ( 4300 2050 ) ( * 2300 )",
    )
    geometry = ads.DefGeometry(text)
    assert geometry.unparsed_tokens == 0, geometry.unparsed_tokens
    wires = [
        (w["x1"], w["y1"], w["x2"], w["y2"], w["width"])
        for w in geometry.nets["_00146_"]
    ]
    assert wires == [(4300, 2050, 4300, 2300, None)], wires


def test_a_taperrule_name_stays_counted_never_geometry() -> None:
    """Control: ``TAPERRULE <name>`` before the points never becomes geometry.

    #1030 changed the *count*, not the safety: the name is now consumed with
    its keyword (as after the points), so it is no longer counted unparsed
    (1 -> 0). The pinned property is that no width/via/point is made of it.
    """
    text = DEF.replace(
        "+ ROUTED Metal2 400 ( 4300 2050 ) ( 4600 2050 )",
        "+ ROUTED Metal2 TAPERRULE NDR_1 ( 4300 2050 ) ( * 2300 )",
    )
    geometry = ads.DefGeometry(text)
    assert geometry.unparsed_tokens == 0, geometry.unparsed_tokens
    assert _vias_00146(geometry) == [], _vias_00146(geometry)
    wires = [
        (w["x1"], w["y1"], w["x2"], w["y2"], w["width"])
        for w in geometry.nets["_00146_"]
    ]
    assert wires == [(4300, 2050, 4300, 2300, None)], wires


def _after_points(extra: str) -> "ads.DefGeometry":
    """The mini-DEF's first path with ``extra`` written after its points."""
    return ads.DefGeometry(
        DEF.replace(
            "+ ROUTED Metal2 400 ( 4300 2050 ) ( 4600 2050 )",
            "+ ROUTED Metal2 ( 4300 2050 ) ( * 2300 ) " + extra,
        )
    )


def _vias_00146(geometry: "ads.DefGeometry") -> list[str]:
    return [s["via"] for s in geometry.nets["_00146_"] if s["kind"] == "via"]


@pytest.mark.parametrize(
    "extra",
    [
        "+ TAPERRULE NDR_1",
        "+ NONDEFAULT ndr1",
        "+ PROPERTY weight 5",
        "+ PROPERTY a 1 b 2",
        "+ ORIGINAL OTHERNET",
    ],
)
def test_a_skip_keyword_name_after_the_points_is_never_a_via(extra: str) -> None:
    """#1030: the name rides with its keyword, never becomes a phantom via."""
    geometry = _after_points(extra)
    assert _vias_00146(geometry) == [], _vias_00146(geometry)
    assert geometry.unparsed_tokens == 0, geometry.unparsed_samples


def test_a_real_via_name_after_a_point_still_emits_its_via() -> None:
    """Control (defeating input): the consume must not eat a genuine via name,
    including on a path that carried ``TAPERRULE <name>`` before its points."""
    plain = ads.DefGeometry(
        DEF.replace(
            "+ ROUTED Metal2 400 ( 4300 2050 ) ( 4600 2050 )",
            "+ ROUTED Metal2 ( 4300 2050 ) via12 ( * 2300 )",
        )
    )
    assert _vias_00146(plain) == ["via12"], _vias_00146(plain)
    tapered = ads.DefGeometry(
        DEF.replace(
            "+ ROUTED Metal2 400 ( 4300 2050 ) ( 4600 2050 )",
            "+ ROUTED Metal2 TAPERRULE NDR_1 ( 4300 2050 ) via12 ( * 2300 )",
        )
    )
    assert _vias_00146(tapered) == ["via12"], _vias_00146(tapered)
    # and a name-taking keyword followed by a real via on the next path
    after = _after_points("+ TAPERRULE NDR_1 NEW Metal3 ( 4300 2300 ) via23")
    assert _vias_00146(after) == ["via23"], _vias_00146(after)


def test_a_property_with_a_dangling_name_ends_cleanly() -> None:
    """Malformed ``PROPERTY weight`` (no value) at end of net: no crash, no via."""
    geometry = _after_points("+ PROPERTY weight")
    assert _vias_00146(geometry) == [], _vias_00146(geometry)


def test_a_property_never_erases_geometry_that_follows_it() -> None:
    """Defeating input (rule 8): geometry after a PROPERTY must survive.

    An unconditional swallow to the next ``+`` passes the no-via tests above
    trivially while erasing a whole path with ``unparsed`` still 0.
    """
    after = _after_points("+ PROPERTY weight 5 NEW Metal3 ( 2000 2000 ) ( * 3000 ) via23")
    assert _vias_00146(after) == ["via23"], _vias_00146(after)
    assert after.unparsed_tokens == 0, after.unparsed_samples
    inline = ads.DefGeometry(
        DEF.replace(
            "+ ROUTED Metal2 400 ( 4300 2050 ) ( 4600 2050 )",
            "+ ROUTED Metal2 PROPERTY lengthSnap 5 ( 4300 2050 ) ( * 2300 ) via12",
        )
    )
    assert _vias_00146(inline) == ["via12"], _vias_00146(inline)
    assert [s for s in inline.nets["_00146_"] if s["kind"] == "wire"], inline.nets


def test_refusal_inputs(tmp_path: pathlib.Path) -> None:
    """Missing inputs and a power-block-less par response refuse; nothing is written."""
    def_path = tmp_path / "x.def"
    def_path.write_text(DEF)
    missing = ads.read_par_power_nets(tmp_path / "absent.json")
    assert missing[0] is None and missing[1] is not None

    par_path = tmp_path / "par.json"
    par_path.write_text('{"requests": {}}')
    no_block = ads.read_par_power_nets(par_path)
    assert no_block[0] is None and "no power block" in no_block[1]

    par_ok = tmp_path / "par_ok.json"
    par_ok.write_text('{"requests": {"place_and_route": {"power": {"nets": ["VSS", "VDD"]}}}}')
    ok = ads.read_par_power_nets(par_ok)
    assert ok[0] == frozenset({"VSS", "VDD"}) and ok[1] is None

    par_klt = tmp_path / "par_klt.json"
    par_klt.write_text('{"power": {"power_net": "VDD", "ground_net": "VSS"}}')
    ok_klt = ads.read_par_power_nets(par_klt)
    assert ok_klt[0] == frozenset({"VDD", "VSS"}), ok_klt

    par_empty = tmp_path / "par_empty.json"
    par_empty.write_text('{"power": {"pdn": true}}')
    no_nets = ads.read_par_power_nets(par_empty)
    assert no_nets[0] is None and "no power/ground nets" in no_nets[1]

    assert def_path.exists()


@pytest.mark.parametrize("name_kw", ["TAPERRULE NDR_1", "ORIGINAL OTHERNET"])
def test_a_name_keyword_with_no_plus_never_becomes_a_via(name_kw: str) -> None:
    """#1030, isolated from #1034: the keyword is written with NO ``+``.

    #1034 clears the via-name position at ``+``, so every ``+ ...`` case above
    is also satisfied by that change. Only this PR's consume-the-argument
    handling covers ``( .. ) ( * .. ) TAPERRULE NDR_1`` -- main's tree
    alone reports ``('via', 'NDR_1')`` here.
    """
    geometry = _after_points(name_kw)
    assert _vias_00146(geometry) == [], geometry.nets["_00146_"]


# ---- the output path (#2096): --output under a missing directory ----------------------
# THE DEFECT, red-first on main @ 8eb04e5b (2026-11-28): ``--output
# NEWDIR/sites.json`` parsed the DEF, analysed every site and then died at
# ``Path(args.output).write_text(...)`` on a FileNotFoundError -- a traceback
# and exit 1, USAGE's code, with the analysis lost. This tool refuses with
# ``EXIT_REFUSED`` (3), and keeps that here.
#
# THE CONTRACT, the #2040 pattern: ``--output`` is checked before the DEF is
# read (a directory given as the file, or a parent that cannot be made, joins
# the other refusals: nothing is read); a missing parent is created; and an
# OSError at the write is REFUSED, never a traceback.
#
# WHAT RUNS FOR REAL: ``main``, the DEF parser, ``analyse`` and the write, on
# the synthetic mini-DEF above. ``DefGeometry`` is wrapped only to count the
# reads, and ``analyse`` once to remove the destination after it ran.


def _inputs(tmp_path: pathlib.Path) -> list[str]:
    d, r, p = tmp_path / "x.def", tmp_path / "x.rpt", tmp_path / "par.json"
    d.write_text(DEF)
    r.write_text("violation type: Metal Spacing\n\tsrcs: _00146_ VSS\n"
                 f"\tbbox = {SITE_PAD}\n\tLayer: Metal2\n")
    p.write_text('{"power": {"power_net": "VDD", "ground_net": "VSS"}}')
    return ["--def", str(d), "--drc-report", str(r), "--par-response", str(p)]


def _count_def_reads(monkeypatch, calls: list) -> None:
    real = ads.DefGeometry

    def counted(text):
        calls.append(text[:8])
        return real(text)

    monkeypatch.setattr(ads, "DefGeometry", counted)


READ_ONLY = pytest.param("read-only", marks=pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root writes into a read-only directory, so nothing refuses"))


def _lose_the_directory(parent: pathlib.Path, how: str) -> None:
    """Between the pre-flight and the write: the directory is removed, or made
    read-only -- the input that passes ``mkdir(exist_ok=True)`` (rule 8)."""
    if how == "removed":
        shutil.rmtree(parent, ignore_errors=True)
    else:
        parent.mkdir(parents=True, exist_ok=True)
        parent.chmod(0o500)


def _an_unwritable_output(tmp_path: pathlib.Path, how: str) -> pathlib.Path:
    if how == "directory":
        return tmp_path
    if how == "file-in-the-way":
        (tmp_path / "file").write_text("not a directory")
        return tmp_path / "file" / "sites.json"
    (tmp_path / "read-only").mkdir()
    (tmp_path / "read-only").chmod(0o500)
    return tmp_path / "read-only" / "new" / "sites.json"


def test_output_under_a_missing_directory_is_created(tmp_path: pathlib.Path, capsys) -> None:
    out = tmp_path / "new" / "dir" / "sites.json"
    assert ads.main(_inputs(tmp_path) + ["--output", str(out)]) == ads.EXIT_OK
    assert json.loads(out.read_text())["verdict_counts"]
    assert f"def_site_analysis: {out}" in capsys.readouterr().out


@pytest.mark.parametrize("how", ["directory", "file-in-the-way", READ_ONLY],
                         ids=["directory-given-as-the-file", "parent-uncreatable", "parent-read-only"])
def test_an_unwritable_output_is_refused_before_the_def_is_read(
        tmp_path: pathlib.Path, monkeypatch, capsys, how: str) -> None:
    out = _an_unwritable_output(tmp_path, how)
    calls: list = []
    _count_def_reads(monkeypatch, calls)
    try:
        code = ads.main(_inputs(tmp_path) + ["--output", str(out)])
        err = capsys.readouterr().err
    finally:
        if how == "read-only":
            (tmp_path / "read-only").chmod(0o700)
    assert code == ads.EXIT_REFUSED, err
    assert "REFUSED" in err, err
    assert calls == []
    assert how == "directory" or not out.exists()


@pytest.mark.parametrize("how", ["removed", READ_ONLY], ids=["directory-removed", "parent-read-only"])
def test_a_write_failure_after_the_analysis_is_refused_not_a_traceback(
        tmp_path: pathlib.Path, monkeypatch, capsys, how: str) -> None:
    """The destination is lost between the pre-flight and the write: the real
    ``write_text`` then raises OSError, which must be REFUSED."""
    out = tmp_path / "dir" / "sites.json"
    real = ads.analyse

    def analyse_then_lose_the_directory(*args):
        sites = real(*args)
        _lose_the_directory(out.parent, how)
        return sites

    monkeypatch.setattr(ads, "analyse", analyse_then_lose_the_directory)
    try:
        code = ads.main(_inputs(tmp_path) + ["--output", str(out)])
        err = capsys.readouterr().err
    finally:
        if how == "read-only":
            out.parent.chmod(0o700)
    assert code == ads.EXIT_REFUSED
    assert "REFUSED" in err and str(out) in err, err
