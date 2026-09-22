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
        if key in record:  # older publications predate newer summary fields
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


def _no_output_delay(tmp_path, unconstrained, false_path=0, timing_clock=0):
    for p in FIXTURE.glob("*.rpt"):
        shutil.copyfile(p, tmp_path / p.name)
    p = tmp_path / "timing.rpt"
    text = p.read_text()
    assert "There are 7 ports with no output delay specified" in text
    text = text.replace("There are 7 ports with no output delay specified",
                        f"There are {unconstrained} ports with no output delay specified")
    text = text.replace("There are 0 ports with no output delay but user has a false path constraint",
                        f"There are {false_path} ports with no output delay but user has a false path constraint")
    clock_sentence = (f"There are {timing_clock} ports with no output delay but "
                      "with a timing clock defined on it or propagating through it")
    if timing_clock == 1:
        clock_sentence = clock_sentence.replace("There are 1 ports", "There is 1 port")
    text = text.replace(
        "There are 0 ports with no output delay but with a timing clock defined on it or propagating through it",
        clock_sentence + "\n\ni2s_bclk\n" if timing_clock else clock_sentence)
    text = text.replace("checking no_output_delay (7)",
                        f"checking no_output_delay ({unconstrained + false_path + timing_clock})")
    p.write_text(text)
    return publish.inspect_reports(tmp_path)


def test_external_io_timing_flag_is_refused_true_without_constrained_outputs(tmp_path):
    # Red-first: external_io_timing_qualified must be true only when the routed
    # report itself shows no unconstrained output ports and no output port
    # excused by a false path. A report doctored to that state qualifies;
    # anything less must keep the flag false.
    assert _no_output_delay(tmp_path, unconstrained=0)[
        "external_io_timing_qualified"] is True


def test_forwarded_clock_exception_is_reported_as_data(tmp_path):
    r = _no_output_delay(tmp_path, unconstrained=0, timing_clock=1)
    assert r["external_io_timing_qualified"] is True
    assert r["output_delay_exceptions"] == ["i2s_bclk"]


def test_false_path_excuses_do_not_qualify_external_io(tmp_path):
    # A false path is not a budget: excusing outputs with false paths must
    # keep the flag false even when no unconstrained port remains.
    assert _no_output_delay(tmp_path, unconstrained=0, false_path=3)[
        "external_io_timing_qualified"] is False


def test_external_io_timing_flag_is_false_while_outputs_remain_unconstrained():
    r = publish.inspect_reports(FIXTURE)
    assert r["missing_output_delays"] == 7
    assert r["external_io_timing_qualified"] is False


def test_published_flag_cannot_disagree_with_the_routed_report():
    record = json.loads((FIXTURE / "publication.json").read_text())
    computed = publish.inspect_reports(FIXTURE)
    for key, value in computed.items():
        if key in record:
            assert record[key] == value, key
    assert record["external_io_timing_qualified"] is False
    assert "output_delay_exceptions" not in record  # predates the exception class
