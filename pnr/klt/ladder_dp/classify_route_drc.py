#!/usr/bin/env python3
"""Classify a klt place-and-route detailed-route DRC report (issue #808).

The finished `ladder_dp` route this issue commits has to answer three
questions about its routing DRC evidence, and prose answers none of them
repeatably:

1. **Is klayout-tools#2089's doubling live at the klt build that ran?**
   Measured, not assumed: total ``violation type:`` records in the report
   vs the number of distinct sites, plus how many duplicates are *adjacent
   pairs* (#2089's signature shape -- the committed 0.5.0 baseline is
   46 records / 23 distinct, every one appearing exactly twice, always
   adjacent), and the number of ``detailed_route`` calls the emitted route
   Tcl aims at that same report path (the multiplier's mechanism: with
   ``max_antenna_repair_iterations = N`` there are N+1 such calls, and the
   pre-fix emitter re-used one ``-output_drc`` path for all of them).
2. **How many DRC numbers are there and do they agree?** The report's own
   counts vs ``route__drc_errors`` in ``ladder_dp_route_metrics.json`` --
   including what ``json.loads`` sees (last occurrence wins) vs what the
   file says overall. Metrics written by klt <= 0.5.0 carry **literal
   duplicate keys** (the committed baseline has 97), so the file is read
   pair-aware, in document order; metrics from current klt instead carry a
   ``klt__repeated_metrics`` block and one authoritative value. Both
   shapes are reported as found, never reconciled by assumption.
3. **What is each residual violation?** Per distinct site: the nets it
   involves, its layer, and a first-pass classification -- all-power-net
   srcs -> ``pdn`` (the par_request power block's geometry: Metal1
   followpins, Metal4/Metal5 straps), no power net -> ``signal``, mixed or
   unparsable -> ``unclassified`` with the reason, never forced into a
   bucket. The layer is recorded alongside as evidence (``on_pdn_layer``);
   whether a residual violation is the PDN's fault or the router's is a
   judgement for ``docs/tool-findings.md``, made on this evidence -- this
   tool classifies, it does not conclude.

Parsing mirrors klt's own ``count_route_drc_violations``
(``place_and_route_reports.py``): records are split on ``^violation
type:``, a record is complete when it yields type / srcs / bbox / layer
(both TritonRoute spellings -- inline ``on Layer Metal2`` and multiline
``Layer: met1`` -- are accepted), fields are whitespace-normalised, and
incomplete records are counted individually, never collapsed on
insufficient evidence. An empty report is zero violations, matching klt's
contract.

Exit codes: ``0`` classified (whatever the numbers say -- a doubled count
is a *result*, not a failure); ``3`` REFUSED, an input is missing or
unusable and nothing is reported rather than a half-answer. ``REFUSED``
is a first-class outcome, per ``CLAUDE.md``.

Usage
-----
::

    python3 pnr/klt/ladder_dp/classify_route_drc.py \
        --drc-report pnr/klt/ladder_dp/.klt/place-and-route/ladder_dp_route_drc.rpt \
        --metrics pnr/klt/ladder_dp/.klt/place-and-route/ladder_dp_route_metrics.json \
        --par-response pnr/klt/ladder_dp/par_response.json \
        --route-tcl pnr/klt/ladder_dp/.klt/place-and-route/pnr_ladder_dp_route.tcl \
        --output pnr/klt/ladder_dp/route_drc_classification.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

EXIT_OK = 0
EXIT_REFUSED = 3

#: Same record shape klt's ``count_route_drc_violations`` matches: a type
#: line (already split off), ``srcs:``, ``bbox =`` and a layer in either
#: TritonRoute spelling. Retain all four identity fields -- two violations
#: at the same coordinates can still differ in type, source or layer.
_DRC_RECORD = re.compile(
    r"([^\n]+)\n\s*srcs:\s*(.*?)\s*bbox\s*=\s*(.*?)"
    r"\s*(?:on Layer\s+|Layer:\s*)(\S+)",
    re.DOTALL,
)

#: A ``detailed_route`` invocation and where its DRC report goes. The
#: emitter writes one line per call; flags may be in any order.
_DETAILED_ROUTE = re.compile(r"detailed_route\s+([^\\\n]*)")
_OUTPUT_DRC = re.compile(r"-output_drc\s+(\S+)")
_REPAIR_ANTENNAS = re.compile(r"^\s*repair_antennas\b", re.M)

#: ``net:NAME`` (the klt/OpenROAD spelling) or a bare token; a srcs entry
#: may carry a parenthesised layer annotation after the net name.
_SRC_TOKEN = re.compile(r"(\S+?)(?:\s*\([^)]*\))?(?:\s+|$)")

SCHEMA_VERSION = 1


def _norm(field: str) -> str:
    """Whitespace-normalise one record field, as klt's counter does."""
    return " ".join(field.split())


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return f"sha256:{h.hexdigest()}"


def read_text_or_none(path: Path) -> tuple[str | None, str]:
    """Return ``(text, "")`` or ``(None, reason)``. Never raises."""
    try:
        return path.read_text(errors="replace"), ""
    except OSError as exc:
        return None, f"cannot read {path}: {exc}"


def parse_srcs(srcs: str) -> list[str]:
    """Net names from a ``srcs:`` field, in order, annotations stripped.

    Handles ``net:_16820_ net:VSS`` (the committed klt/OpenROAD spelling)
    and a bare ``VSS (Metal2)`` shape. The ``net:`` prefix removal is
    textual, so a backslash-escaped ``\\net:...`` spelling is **not**
    handled: its backslash survives as a spurious token of its own. No
    committed report uses that spelling and klt's own counter never
    parses srcs names, so it is documented as unsupported rather than
    silently renamed -- renaming would corrupt the net the classification
    keys on (issue #864, which dropped the earlier "kept verbatim"
    claim as unestablished).
    """
    nets: list[str] = []
    stripped = srcs.replace("net:", " ")
    for token in stripped.split():
        token = re.sub(r"\([^)]*\)$", "", token)
        if token:
            nets.append(token)
    return nets


class Site:
    """One distinct violation site and every record that reported it."""

    def __init__(self, rtype: str, srcs: str, bbox: str, layer: str) -> None:
        self.type = rtype
        self.srcs = srcs
        self.bbox = bbox
        self.layer = layer
        self.record_indices: list[int] = []
        self.nets = parse_srcs(srcs)

    @property
    def key3(self) -> tuple[str, str, str]:
        return (self.type, self.srcs, self.bbox)

    @property
    def key4(self) -> tuple[str, str, str, str]:
        return (self.type, self.srcs, self.bbox, self.layer)


def classify_site(
    site: Site, power_nets: frozenset[str], pdn_layers: frozenset[str]
) -> tuple[str, str, bool]:
    """Return ``(classification, reason, on_pdn_layer)`` for one site.

    All srcs power nets -> ``pdn``; none -> ``signal``; a mix, or no nets
    parsed at all, -> ``unclassified`` with the reason -- never forced.
    """
    on_pdn_layer = site.layer in pdn_layers
    if not site.nets:
        return "unclassified", "no nets parsed from srcs", on_pdn_layer
    powers = [n for n in site.nets if n in power_nets]
    if powers and len(powers) == len(site.nets):
        return "pdn", f"all srcs are power nets ({', '.join(site.nets)})", on_pdn_layer
    if not powers:
        return "signal", "no power net in srcs", on_pdn_layer
    return (
        "unclassified",
        f"mixed power ({', '.join(powers)}) and signal srcs",
        on_pdn_layer,
    )


def parse_drc_report(text: str) -> dict[str, Any]:
    """Parse one ``*_route_drc.rpt`` into records, sites and counts.

    Mirrors klt's ``count_route_drc_violations``: split on ``^violation
    type:``, complete records dedup on their normalised fields, incomplete
    records are counted individually. An empty report is zero.
    """
    segments = re.split(r"(?m)^violation type:[ \t]*", text)[1:]
    sites: dict[tuple[str, str, str, str], Site] = {}
    by_key3: Counter = Counter()
    incomplete: list[dict[str, Any]] = []
    adjacent_pairs = 0
    prev_key4: tuple[str, str, str, str] | None = None
    total = 0
    for index, segment in enumerate(segments):
        total += 1
        match = _DRC_RECORD.match(segment)
        if match is None:
            incomplete.append(
                {"record_index": index, "raw": segment.strip()[:400]}
            )
            prev_key4 = None
            continue
        fields = tuple(_norm(field) for field in match.groups())
        site = sites.setdefault(fields, Site(*fields))
        site.record_indices.append(index)
        by_key3[site.key3] += 1
        if fields == prev_key4:
            adjacent_pairs += 1
        prev_key4 = fields
    distinct4 = len(sites)
    distinct3 = len(by_key3)
    return {
        "total_records": total,
        "distinct_sites_type_srcs_bbox": distinct3,
        "distinct_sites_type_srcs_bbox_layer": distinct4,
        "incomplete_records": len(incomplete),
        "adjacent_duplicate_pairs": adjacent_pairs,
        "doubling": {
            "total_vs_distinct_disagree": total != distinct4,
            "observed_multiplier_total_vs_distinct": (
                round(total / distinct4, 3) if distinct4 else None
            ),
            "distinct3_vs_distinct4_disagree": distinct3 != distinct4,
        },
        "incomplete": incomplete,
        "sites": sites,
        "by_key3": by_key3,
    }


def read_metrics(path: Path) -> tuple[dict[str, Any] | None, str]:
    """Read a route-metrics JSON **pair-aware**, in document order.

    klt <= 0.5.0 serialises the engine's metric pairs with literal
    duplicate keys (the committed baseline carries 97), where
    ``json.loads`` silently keeps only the last occurrence. This walk
    records *every* occurrence of the DRC-bearing keys in file order and
    also reports what a plain ``json.loads`` would see, so the two
    readings can disagree on the page. Current klt instead writes one
    authoritative value plus a ``klt__repeated_metrics`` history block;
    that block is reported when present. A root that is not an object
    (a list, a bare scalar, ``null``) is a refusal reason, mirroring
    ``read_par_response``. Returns ``(report, "")`` or ``(None, reason)``.
    """
    text, reason = read_text_or_none(path)
    if text is None:
        return None, reason
    interesting = re.compile(
        r"^(route__drc_errors|route_pass:.*__route__drc_errors)$"
    )
    repeated_block: dict[str, Any] | None = None
    object_calls: list[list[tuple[str, Any]]] = []

    def hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        nonlocal repeated_block
        for key, value in pairs:
            if key == "klt__repeated_metrics" and isinstance(value, dict):
                repeated_block = value
        object_calls.append(list(pairs))
        return dict(pairs)

    try:
        pair_view = json.loads(text, object_pairs_hook=hook)
    except json.JSONDecodeError as exc:
        return None, f"{path} is not parseable JSON: {exc}"
    if not isinstance(pair_view, dict):
        return None, f"{path} is not a JSON object"

    # object_pairs_hook fires post-order, so the root object's call is
    # last; occurrences are a TOP-LEVEL reading only. The
    # klt__repeated_metrics history block legitimately carries a nested
    # route__drc_errors of its own, and counting it here would pass a
    # history list off as a duplicate key.
    root_pairs = object_calls[-1] if object_calls else []
    occurrences: dict[str, list[Any]] = {}
    for key, value in root_pairs:
        if interesting.match(key):
            occurrences.setdefault(key, []).append(value)

    drc = occurrences.get("route__drc_errors", [])
    pass_scoped = {
        key: values
        for key, values in occurrences.items()
        if key.startswith("route_pass:")
    }
    return {
        "route__drc_errors": {
            "occurrences": drc,
            "occurrence_count": len(drc),
            "first": drc[0] if drc else None,
            "plain_json_loads_sees": (
                pair_view.get("route__drc_errors") if pair_view else None
            ),
            "has_duplicate_keys": len(drc) > 1,
        },
        "pass_scoped": pass_scoped,
        "klt__repeated_metrics": repeated_block,
    }, ""


def read_route_tcl(
    path: Path, drc_report_path: Path
) -> tuple[dict[str, Any] | None, str]:
    """Count the route Tcl's ``detailed_route`` calls per report path.

    The multiplier behind #2089 is mechanical: each antenna-repair pass
    re-emits ``detailed_route``, and the pre-fix emitter aimed every call
    at the same ``-output_drc`` path, so the final report holds one copy
    per pass. ``expected_multiplier`` is how many calls target the report
    this run classified -- measured from the emitted Tcl, not assumed to
    be N+1.
    """
    text, reason = read_text_or_none(path)
    if text is None:
        return None, reason
    calls: list[str | None] = []
    for match in _DETAILED_ROUTE.finditer(text):
        target = _OUTPUT_DRC.search(match.group(1))
        calls.append(target.group(1) if target else None)
    repairs = len(_REPAIR_ANTENNAS.findall(text))
    basename = drc_report_path.name
    to_final = [
        c for c in calls if c is not None and (c == str(drc_report_path) or c.endswith("/" + basename) or c == basename)
    ]
    return {
        "detailed_route_calls": len(calls),
        "calls_with_output_drc": sum(1 for c in calls if c is not None),
        "calls_to_this_report_path": len(to_final),
        "expected_multiplier": len(to_final) or None,
        "repair_antennas_calls": repairs,
    }, ""


def read_par_response(path: Path) -> tuple[dict[str, Any] | None, str]:
    """``stage_reached`` and ``route_drc_violation_count`` from a response."""
    try:
        doc = json.loads(path.read_text())
    except OSError as exc:
        return None, f"cannot read {path}: {exc}"
    except json.JSONDecodeError as exc:
        return None, f"{path} is not parseable JSON: {exc}"
    if not isinstance(doc, dict):
        return None, f"{path} is not a JSON object"
    return {
        "stage_reached": doc.get("stage_reached"),
        "route_drc_violation_count": doc.get("route_drc_violation_count"),
    }, ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Classify a klt place-and-route detailed-route DRC report: "
            "#2089 doubling evidence, metrics cross-check, per-site "
            "pdn/signal classification (issue #808)."
        )
    )
    parser.add_argument(
        "--drc-report",
        required=True,
        help="the *_route_drc.rpt to classify (empty file = zero violations)",
    )
    parser.add_argument(
        "--metrics",
        required=True,
        help="the matching *_route_metrics.json (read pair-aware)",
    )
    parser.add_argument(
        "--par-response",
        help="optional par_response.json for the response-side count",
    )
    parser.add_argument(
        "--route-tcl",
        help="optional emitted route .tcl for the multiplier mechanism",
    )
    parser.add_argument(
        "--power-nets",
        default="VSS,VDD",
        help="comma-separated power net names (default: VSS,VDD)",
    )
    parser.add_argument(
        "--pdn-layers",
        default="Metal1,Metal4,Metal5",
        help=(
            "comma-separated PDN layers, recorded as per-site evidence "
            "(default: the par_request power block's Metal1/Metal4/Metal5)"
        ),
    )
    parser.add_argument(
        "--output",
        help="write the classification JSON here (default: stdout)",
    )
    args = parser.parse_args(argv)

    report_path = Path(args.drc_report)
    metrics_path = Path(args.metrics)

    refused: list[str] = []
    text: str | None = None
    if not report_path.exists():
        refused.append(f"--drc-report {report_path} does not exist")
    else:
        # Read here, inside the refusal-collection block, so an
        # existing-but-unreadable report (a directory, permissions)
        # refuses with the documented exit 3 instead of the assert that
        # used to stand in for this check (issue #864).
        text, report_reason = read_text_or_none(report_path)
        if text is None:
            refused.append(report_reason)
    metrics, metrics_reason = read_metrics(metrics_path)
    if metrics is None:
        refused.append(metrics_reason)

    par_response: dict[str, Any] | None = None
    if args.par_response:
        par_response, reason = read_par_response(Path(args.par_response))
        if par_response is None:
            refused.append(reason)

    route_tcl: dict[str, Any] | None = None
    if args.route_tcl:
        route_tcl, reason = read_route_tcl(Path(args.route_tcl), report_path)
        if route_tcl is None:
            refused.append(reason)

    if refused:
        print("REFUSED -- inputs not usable, nothing classified:", file=sys.stderr)
        for problem in refused:
            print(f"  - {problem}", file=sys.stderr)
        return EXIT_REFUSED

    # `refused` above is empty, so `text` is a str: every path that leaves
    # it None appended a refusal first.
    parsed = parse_drc_report(text)

    power_nets = frozenset(args.power_nets.split(","))
    pdn_layers = frozenset(args.pdn_layers.split(","))

    sites_out: list[dict[str, Any]] = []
    summary: Counter = Counter()
    for fields, site in sorted(parsed["sites"].items()):
        classification, reason, on_pdn_layer = classify_site(
            site, power_nets, pdn_layers
        )
        summary[classification] += 1
        sites_out.append(
            {
                "type": site.type,
                "srcs": site.srcs,
                "bbox": site.bbox,
                "layer": site.layer,
                "record_count": len(site.record_indices),
                "record_indices": site.record_indices,
                "nets": site.nets,
                "classification": classification,
                "classification_reason": reason,
                "on_pdn_layer": on_pdn_layer,
            }
        )

    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "inputs": {
            "drc_report": {
                "path": str(report_path),
                "sha256": sha256_file(report_path),
            },
            "metrics": {"path": str(metrics_path), "sha256": sha256_file(metrics_path)},
            "route_tcl": str(args.route_tcl) if args.route_tcl else None,
            "par_response": str(args.par_response) if args.par_response else None,
        },
        "drc_report": {
            key: parsed[key]
            for key in (
                "total_records",
                "distinct_sites_type_srcs_bbox",
                "distinct_sites_type_srcs_bbox_layer",
                "incomplete_records",
                "adjacent_duplicate_pairs",
                "doubling",
            )
        },
        "classification_summary": {
            "distinct_sites_pdn": summary["pdn"],
            "distinct_sites_signal": summary["signal"],
            "distinct_sites_unclassified": summary["unclassified"],
        },
        "sites": sites_out,
        "incomplete_records": parsed["incomplete"],
        "metrics": metrics,
        "route_tcl": route_tcl,
        "par_response": par_response,
    }

    payload = json.dumps(result, indent=2) + "\n"
    if args.output:
        out_path = Path(args.output)
        out_path.write_text(payload)
        print(f"wrote {out_path}", file=sys.stderr)
    else:
        sys.stdout.write(payload)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
