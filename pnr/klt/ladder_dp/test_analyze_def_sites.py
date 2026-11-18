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

And the refusal path: a site whose bbox is not where any geometry lives, and
inputs that are missing, must produce ``REFUSED``/no verdict rather than a
half-answer.
"""
from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
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
