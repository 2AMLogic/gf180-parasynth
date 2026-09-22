import json
import shutil
import pytest
import publish_arty as publish

FIXTURE = publish.ROOT / "fpga/reports/arty/vivado-2025.1"


def test_published_artifacts_match_recorded_hashes_and_build_identity():
    record = json.loads((FIXTURE / "publication.json").read_text())
    build = json.loads((FIXTURE / "report.json").read_text())
    expected_files = {"report.json", "build.tcl", "arty.bit", "timing.rpt",
                      "clocks.rpt", "utilization.rpt", "drc.rpt"}
    assert set(record["published_sha256"]) == expected_files
    for name, digest in record["published_sha256"].items():
        assert publish.build.sha(FIXTURE / name) == digest, name
    assert record["bitstream_sha256"] == build["artifact_sha256"]["arty.bit"]
    assert record["source_sha256"] == build["source_sha256"]
    assert record["verification"] == build["verification"]
    assert record["configuration"] == build["configuration"] == publish.build.CONFIG
    for key, value in publish.inspect_reports(FIXTURE).items():
        assert record[key] == value, key


def test_real_vivado_report_preserves_scope_and_final_not_estimated_timing():
    r = publish.inspect_reports(FIXTURE)
    assert r["timing"]["wns_ns"] == 46.498
    assert r["timing"]["whs_ns"] == 0.050
    assert r["core_period_ns"] == 81.380
    assert r["resources"]["DSPs"] == {"used": 100, "available": 240}
    assert r["resources"]["Slice LUTs"]["used"] == 12695
    assert r["missing_output_delays"] == 7
    assert r["drc"]["DPREG-4"]["count"] == 13
    assert r["external_io_timing_qualified"] is False
    assert r["dsp_feedback_review_complete"] is False


@pytest.mark.parametrize("filename,before,after", [
    ("timing.rpt", "46.498", "-0.001"),
    ("timing.rpt", "0.050", "-0.001"),
    ("timing.rpt", "unconstrained_internal_endpoints (0)", "unconstrained_internal_endpoints (1)"),
    ("clocks.rpt", "81.380", "80.000"),
    ("utilization.rpt", "|  100 |", "|  241 |"),
    ("drc.rpt", "| Warning  |", "| Error    |"),
])
def test_real_report_mutations_cannot_publish_a_timing_or_fit_pass(tmp_path, filename, before, after):
    for p in FIXTURE.glob("*.rpt"):
        shutil.copyfile(p, tmp_path / p.name)
    p = tmp_path / filename
    text = p.read_text()
    assert before in text
    p.write_text(text.replace(before, after))
    with pytest.raises(ValueError):
        publish.inspect_reports(tmp_path)


def test_missing_timing_report_refuses(tmp_path):
    with pytest.raises(ValueError):
        publish.inspect_reports(tmp_path)
