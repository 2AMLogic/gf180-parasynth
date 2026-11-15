"""Tests for ``classify_route_drc.py`` (issue #808).

Pure parsing/classification: no klt, no openroad, no fixtures bigger than
a string. Three of these are grounded in artefacts this repository
actually ships rather than in fixtures invented here:

* ``pnr/orfs/evidence/synth_top/placeholder-2a88c35/5_route_drc.rpt`` is a
  real TritonRoute report showing #2089's signature shape -- two records,
  one site, adjacent -- from a different flow (ORFS, not klt). If the
  parser cannot read a real report, a synthetic one proves nothing.
* ``pnr/orfs/evidence/synth_top/joined-d1e5068/5_route_drc.rpt`` is the
  real zero-DRC run: 0 bytes. An empty report is zero, matching klt's own
  contract; a parser that crashes or guesses on it would turn a clean
  route into noise.

What these tests do NOT pin, stated rather than left to be discovered
(``docs/verification-rules.md`` rule 8): they cannot know what the *next*
run's report looks like. A future TritonRoute that emits a field the
record regex does not cover lands in ``incomplete_records`` -- counted
individually, never silently dropped -- which the incomplete-record test
below pins as behaviour, not as a failure. The classification is likewise
evidence, not verdict: ``unclassified`` is a first-class bucket and the
"PDN's fault or the router's" judgement lives in ``docs/tool-findings.md``.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import classify_route_drc  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
REAL_DOUBLED_REPORT = (
    HERE.parents[1]
    / "orfs"
    / "evidence"
    / "synth_top"
    / "placeholder-2a88c35"
    / "5_route_drc.rpt"
)
REAL_EMPTY_REPORT = (
    HERE.parents[1]
    / "orfs"
    / "evidence"
    / "synth_top"
    / "joined-d1e5068"
    / "5_route_drc.rpt"
)


def _record(rtype: str, srcs: str, x: float, y: float, layer: str) -> str:
    """One record in the exact spelling the committed real report uses."""
    return (
        f"violation type: {rtype}\n"
        f"\tsrcs: {srcs}\n"
        f"\tbbox = ({x:.4f}, {y:.4f}) - ({x + 0.28:.4f}, {y + 0.268:.4f}) "
        f"on Layer {layer}\n"
    )


# ── the parser on real, committed reports ────────────────────────────────


@pytest.mark.skipif(
    not REAL_DOUBLED_REPORT.exists(), reason="committed report missing"
)
def test_the_committed_doubled_report_is_two_records_one_site():
    """Real artefact: #2089's shape, from a flow that never ran klt.

    ``docs/pnr-synth-top.md`` §3 records this file as "one site listed
    twice"; the parser must agree with the doc, not with a paraphrase.
    """
    parsed = classify_route_drc.parse_drc_report(REAL_DOUBLED_REPORT.read_text())
    assert parsed["total_records"] == 2
    assert parsed["distinct_sites_type_srcs_bbox"] == 1
    assert parsed["distinct_sites_type_srcs_bbox_layer"] == 1
    assert parsed["adjacent_duplicate_pairs"] == 1
    assert parsed["doubling"]["observed_multiplier_total_vs_distinct"] == 2.0


@pytest.mark.skipif(
    not REAL_EMPTY_REPORT.exists(), reason="committed report missing"
)
def test_the_committed_clean_report_is_zero():
    """Real artefact: the joined run routed clean -- 0 bytes on disk.

    klt's contract: an empty report means zero. None, NaN or a crash here
    would misreport a clean route.
    """
    parsed = classify_route_drc.parse_drc_report(REAL_EMPTY_REPORT.read_text())
    assert parsed["total_records"] == 0
    assert parsed["distinct_sites_type_srcs_bbox_layer"] == 0
    assert parsed["doubling"]["observed_multiplier_total_vs_distinct"] is None


# ── the parser on synthetic fixtures ─────────────────────────────────────


def test_no_duplicates_makes_total_and_distinct_agree():
    """The Test Plan's second case: no duplicates, counts agree."""
    text = (
        _record("Metal Spacing", "net:a net:b", 1.0, 2.0, "Metal2")
        + _record("Short", "net:c net:VSS", 3.0, 4.0, "Metal1")
        + _record("Metal Spacing", "net:a net:d", 5.0, 6.0, "Metal4")
    )
    parsed = classify_route_drc.parse_drc_report(text)
    assert parsed["total_records"] == 3
    assert parsed["distinct_sites_type_srcs_bbox_layer"] == 3
    assert not parsed["doubling"]["total_vs_distinct_disagree"]
    assert parsed["doubling"]["observed_multiplier_total_vs_distinct"] == 1.0
    assert parsed["incomplete_records"] == 0


def test_same_bbox_different_type_is_two_sites():
    """Identity is the full record, not the coordinates alone."""
    text = _record("Metal Spacing", "net:a", 1.0, 2.0, "Metal2") + _record(
        "Short", "net:a", 1.0, 2.0, "Metal2"
    )
    parsed = classify_route_drc.parse_drc_report(text)
    assert parsed["total_records"] == 2
    assert parsed["distinct_sites_type_srcs_bbox_layer"] == 2


def test_incomplete_records_are_counted_not_collapsed():
    """A record the regex cannot complete is counted individually.

    klt's counter does the same; silently dropping it would understate a
    count the acceptance criteria read.
    """
    text = (
        _record("Metal Spacing", "net:a net:b", 1.0, 2.0, "Metal2")
        + "violation type: Metal Spacing\n\tsrcs: net:c\n"
    )
    parsed = classify_route_drc.parse_drc_report(text)
    assert parsed["total_records"] == 2
    assert parsed["incomplete_records"] == 1
    assert parsed["distinct_sites_type_srcs_bbox_layer"] == 1
    assert parsed["incomplete"][0]["raw"].startswith("Metal Spacing")


def test_both_tritonroute_layer_spellings_parse():
    """Inline ``on Layer Metal2`` and multiline ``Layer: met1``."""
    text = _record("Metal Spacing", "net:a", 1.0, 2.0, "Metal2") + (
        "violation type: Spacing\n"
        "\tsrcs: VSS (met1)\n"
        "\tbbox = (3.0, 4.0) - (3.5, 4.5)\n"
        "\tLayer: met1\n"
    )
    parsed = classify_route_drc.parse_drc_report(text)
    layers = {site.layer for site in parsed["sites"].values()}
    assert layers == {"Metal2", "met1"}


def test_whitespace_normalised_fields_dedupe():
    """klt normalises each field's whitespace before deduping; so must we.

    A report differing only in indentation is one site, not two.
    """
    text = (
        _record("Metal Spacing", "net:a  net:b", 1.0, 2.0, "Metal2")
        + "violation type: Metal Spacing\n\tsrcs: net:a net:b\n"
        "\tbbox = (1.0000,   2.0000) - (1.2800, 2.2680)  on Layer Metal2\n"
    )
    parsed = classify_route_drc.parse_drc_report(text)
    assert parsed["total_records"] == 2
    assert parsed["distinct_sites_type_srcs_bbox_layer"] == 1
    assert parsed["adjacent_duplicate_pairs"] == 1


# ── classification ────────────────────────────────────────────────────────


def _one_site(srcs: str, layer: str = "Metal2") -> classify_route_drc.Site:
    return classify_route_drc.Site("Metal Spacing", srcs, "(0 0) (1 1)", layer)


def test_all_power_srcs_classify_pdn():
    classification, _, on_pdn = classify_route_drc.classify_site(
        _one_site("net:VSS net:VDD", "Metal4"),
        frozenset({"VSS", "VDD"}),
        frozenset({"Metal1", "Metal4", "Metal5"}),
    )
    assert classification == "pdn"
    assert on_pdn is True


def test_signal_only_srcs_classify_signal():
    classification, reason, on_pdn = classify_route_drc.classify_site(
        _one_site("net:_16820_ net:_42_", "Metal3"),
        frozenset({"VSS", "VDD"}),
        frozenset({"Metal1", "Metal4", "Metal5"}),
    )
    assert classification == "signal"
    assert "no power net" in reason
    assert on_pdn is False


def test_mixed_power_and_signal_is_unclassified_not_forced():
    """The neither-nor edge case: a signal net against a rail is recorded
    unclassified with the reason, never bucketed to fit."""
    classification, reason, _ = classify_route_drc.classify_site(
        _one_site("net:_16820_ net:VSS", "Metal2"),
        frozenset({"VSS", "VDD"}),
        frozenset({"Metal1", "Metal4", "Metal5"}),
    )
    assert classification == "unclassified"
    assert "mixed" in reason


@pytest.mark.skipif(
    not REAL_DOUBLED_REPORT.exists(), reason="committed report missing"
)
def test_the_committed_doubled_report_classifies_unclassified():
    """The real report's srcs are ``net:_16820_ net:VSS`` -- mixed.

    ``docs/tool-findings.md`` calls these "violations between signal nets
    and VSS"; the instrument must not claim more than that.
    """
    parsed = classify_route_drc.parse_drc_report(REAL_DOUBLED_REPORT.read_text())
    (site,) = parsed["sites"].values()
    classification, reason, _ = classify_route_drc.classify_site(
        site, frozenset({"VSS", "VDD"}), frozenset({"Metal1", "Metal4", "Metal5"})
    )
    assert site.nets == ["_16820_", "VSS"]
    assert classification == "unclassified"
    assert "mixed" in reason


def test_unparsable_srcs_is_unclassified_with_reason():
    classification, reason, _ = classify_route_drc.classify_site(
        _one_site("(unparsable)"), frozenset({"VSS"}), frozenset()
    )
    assert classification == "unclassified"
    assert "no nets" in reason


# ── the metrics reader ────────────────────────────────────────────────────

DUPLICATE_KEY_METRICS = (
    # The committed 0.5.0 baseline's shape: literal duplicate keys, first
    # 23 then 46, plus a pass-scoped key. json.loads keeps the last.
    '{"route__drc_errors": 23,'
    ' "route_pass:0__route__drc_errors": 9,'
    ' "timing__fmax__route": 28.04,'
    ' "route__drc_errors": 46}'
)

CURRENT_FORMAT_METRICS = json.dumps(
    {
        "route__drc_errors": 23,
        "klt__repeated_metrics": {"route__drc_errors": [23, 46]},
    }
)


def test_duplicate_key_metrics_reports_every_occurrence(tmp_path):
    """The cleaner mechanical check, with both readings side by side.

    The committed baseline has ``route__drc_errors`` twice (23 then 46);
    ``json.loads`` sees 46. The instrument must show both, not reconcile.
    """
    path = tmp_path / "metrics.json"
    path.write_text(DUPLICATE_KEY_METRICS)
    metrics, reason = classify_route_drc.read_metrics(path)
    assert reason == ""
    drc = metrics["route__drc_errors"]
    assert drc["occurrences"] == [23, 46]
    assert drc["first"] == 23
    assert drc["plain_json_loads_sees"] == 46
    assert drc["has_duplicate_keys"] is True
    assert metrics["pass_scoped"] == {"route_pass:0__route__drc_errors": [9]}


def test_current_format_metrics_reports_the_history_block(tmp_path):
    path = tmp_path / "metrics.json"
    path.write_text(CURRENT_FORMAT_METRICS)
    metrics, reason = classify_route_drc.read_metrics(path)
    assert reason == ""
    drc = metrics["route__drc_errors"]
    assert drc["occurrences"] == [23]
    assert drc["has_duplicate_keys"] is False
    assert metrics["klt__repeated_metrics"] == {"route__drc_errors": [23, 46]}


def test_unparseable_metrics_is_refused_not_guessed(tmp_path):
    path = tmp_path / "metrics.json"
    path.write_text("{not json")
    metrics, reason = classify_route_drc.read_metrics(path)
    assert metrics is None
    assert "not parseable" in reason


# ── the route-Tcl multiplier reader ──────────────────────────────────────


PRE_FIX_TCL = (
    "# emitted route stage\n"
    "detailed_route -output_drc /run/ladder_dp_route_drc.rpt"
    " -output_maze /run/ladder_dp_route_maze.log -or_seed 1\n"
    "repair_antennas gf180mcu_fd_sc_mcu7t5v0__antenna\n"
    "detailed_route -output_drc /run/ladder_dp_route_drc.rpt"
    " -output_maze /run/ladder_dp_route_maze.log -or_seed 1\n"
)

PER_PASS_TCL = (
    "detailed_route -output_drc /run/ladder_dp_route_pass_0_drc.rpt"
    " -output_maze /run/ladder_dp_route_pass_0_maze.log -or_seed 1\n"
    "repair_antennas gf180mcu_fd_sc_mcu7t5v0__antenna\n"
    "detailed_route -output_drc /run/ladder_dp_route_drc.rpt"
    " -output_maze /run/ladder_dp_route_maze.log -or_seed 1\n"
)


def test_pre_fix_tcl_counts_two_calls_to_the_final_report(tmp_path):
    """#2089's mechanism, measured: one path written by N+1 calls."""
    tcl = tmp_path / "route.tcl"
    tcl.write_text(PRE_FIX_TCL)
    info, reason = classify_route_drc.read_route_tcl(
        tcl, pathlib.Path("/run/ladder_dp_route_drc.rpt")
    )
    assert reason == ""
    assert info["detailed_route_calls"] == 2
    assert info["calls_to_this_report_path"] == 2
    assert info["expected_multiplier"] == 2
    assert info["repair_antennas_calls"] == 1


def test_per_pass_tcl_expects_no_doubling(tmp_path):
    """Current klt gives each pass its own report path: multiplier 1."""
    tcl = tmp_path / "route.tcl"
    tcl.write_text(PER_PASS_TCL)
    info, _ = classify_route_drc.read_route_tcl(
        tcl, pathlib.Path("/run/ladder_dp_route_drc.rpt")
    )
    assert info["detailed_route_calls"] == 2
    assert info["calls_to_this_report_path"] == 1
    assert info["expected_multiplier"] == 1


# ── the CLI ───────────────────────────────────────────────────────────────


def _write_inputs(tmp_path: pathlib.Path, report_text: str, metrics_text: str):
    report = tmp_path / "top_route_drc.rpt"
    report.write_text(report_text)
    metrics = tmp_path / "top_route_metrics.json"
    metrics.write_text(metrics_text)
    return report, metrics


def test_main_classifies_and_writes_output(tmp_path, capsys):
    report, metrics = _write_inputs(
        tmp_path,
        _record("Metal Spacing", "net:VSS net:VDD", 1.0, 2.0, "Metal4") * 2,
        '{"route__drc_errors": 2}',
    )
    tcl = tmp_path / "route.tcl"
    tcl.write_text(
        "detailed_route -output_drc "
        f"{report} -output_maze {tmp_path}/top_route_maze.log -or_seed 1\n"
        "repair_antennas gf180mcu_fd_sc_mcu7t5v0__antenna\n"
        f"detailed_route -output_drc {report} -output_maze {tmp_path}/top_route_maze.log -or_seed 1\n"
    )
    out = tmp_path / "classification.json"
    rc = classify_route_drc.main(
        [
            "--drc-report",
            str(report),
            "--metrics",
            str(metrics),
            "--route-tcl",
            str(tcl),
            "--output",
            str(out),
        ]
    )
    assert rc == classify_route_drc.EXIT_OK
    doc = json.loads(out.read_text())
    assert doc["drc_report"]["total_records"] == 2
    assert doc["drc_report"]["distinct_sites_type_srcs_bbox_layer"] == 1
    assert doc["classification_summary"]["distinct_sites_pdn"] == 1
    assert doc["route_tcl"]["expected_multiplier"] == 2


def test_missing_report_is_refused_with_no_output(tmp_path, capsys):
    report, metrics = _write_inputs(tmp_path, "", "{}")
    rc = classify_route_drc.main(
        [
            "--drc-report",
            str(tmp_path / "absent.rpt"),
            "--metrics",
            str(metrics),
        ]
    )
    assert rc == classify_route_drc.EXIT_REFUSED
    assert "REFUSED" in capsys.readouterr().err


def test_unparseable_metrics_is_refused(tmp_path, capsys):
    report, _ = _write_inputs(tmp_path, _record("Short", "net:a", 1, 2, "Metal2"), "")
    (tmp_path / "top_route_metrics.json").write_text("{oops")
    rc = classify_route_drc.main(
        ["--drc-report", str(report), "--metrics", str(tmp_path / "top_route_metrics.json")]
    )
    assert rc == classify_route_drc.EXIT_REFUSED


def test_empty_report_classifies_as_zero(tmp_path, capsys):
    """Empty report + clean metrics: a real clean route, not an error."""
    report, metrics = _write_inputs(tmp_path, "", '{"route__drc_errors": 0}')
    rc = classify_route_drc.main(
        ["--drc-report", str(report), "--metrics", str(metrics)]
    )
    assert rc == classify_route_drc.EXIT_OK
    doc = json.loads(capsys.readouterr().out)
    assert doc["drc_report"]["total_records"] == 0
    assert doc["drc_report"]["distinct_sites_type_srcs_bbox_layer"] == 0
