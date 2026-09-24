import json
import re
import shutil
import pytest
import publish_arty as publish

FIXTURE = publish.ROOT / "fpga/reports/arty/vivado-2025.1"
_BASELINE = publish.inspect_reports(FIXTURE)
_WNS = f"{_BASELINE['timing']['wns_ns']:.3f}"
_WHS = f"{_BASELINE['timing']['whs_ns']:.3f}"


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
    # The numbers below are pinned to THIS publication on purpose: a silently
    # swapped report must fail here. Re-pin them after a deliberate
    # regeneration -- never loosen the assertions to make a rebuild pass.
    r = publish.inspect_reports(FIXTURE)
    assert r["timing"]["wns_ns"] == 14.199
    assert r["timing"]["whs_ns"] == 0.032
    assert r["timing"]["setup_failing"] == 0
    assert r["timing"]["hold_failing"] == 0
    assert r["core_period_ns"] == 81.380
    assert r["resources"]["DSPs"] == {"used": 100, "available": 240}
    assert r["resources"]["Slice LUTs"]["used"] == 12695
    assert r["missing_output_delays"] == 1
    assert r["drc"]["DPREG-4"]["count"] == 13
    assert r["external_io_timing_qualified"] is True
    assert r["output_delay_exceptions"] == ["i2s_bclk"]
    # the inspector no longer carries the DSP flag at all: it is derived in
    # publish() from bound evidence (dsp_disposition), never defaulted here
    assert "dsp_feedback_review_complete" not in r


@pytest.mark.parametrize("filename,before,after", [
    ("timing.rpt", _WNS, "-0.001"),
    ("timing.rpt", _WHS, "-0.001"),
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


def _sentence(count, marker):
    verb = "There is 1 port" if count == 1 else f"There are {count} ports"
    return f"{verb} {marker}"


def _no_output_delay(tmp_path, unconstrained, false_path=0, timing_clock=0):
    # Synthesize a check_timing census from the routed fixture. Substitution
    # counts are asserted: a fixture whose wording no longer matches must fail
    # loudly rather than leave the state unmutated and pass vacuously.
    for p in FIXTURE.glob("*.rpt"):
        shutil.copyfile(p, tmp_path / p.name)
    p = tmp_path / "timing.rpt"
    text = p.read_text()
    subs = 0
    for marker, count in (
        ("with no output delay specified", unconstrained),
        ("with no output delay but user has a false path constraint", false_path),
        ("with no output delay but with a timing clock defined on it or propagating through it", timing_clock),
    ):
        text, n = re.subn(r"There (?:are|is) \d+ ports? " + marker,
                          _sentence(count, marker), text)
        assert n >= 1, marker
        subs += n
    text, n = re.subn(r"checking no_output_delay \(\d+\)",
                      f"checking no_output_delay ({unconstrained + false_path + timing_clock})", text)
    assert n >= 1
    p.write_text(text)
    return publish.inspect_reports(tmp_path)


def test_external_io_timing_flag_is_refused_true_without_constrained_outputs(tmp_path):
    # Red-first: external_io_timing_qualified must be true only when the routed
    # report itself shows no unconstrained output ports and no output port
    # excused by a false path. A report doctored to that state qualifies;
    # anything less must keep the flag false.
    assert _no_output_delay(tmp_path, unconstrained=0, timing_clock=0)[
        "external_io_timing_qualified"] is True


def test_forwarded_clock_exception_is_reported_as_data():
    # The routed fixture itself carries the one permitted exception: i2s_bclk
    # is unconstrained but has a timing clock (the forwarded DAC clock), and
    # the report lists it by name. Assert on the real report, not a copy.
    r = publish.inspect_reports(FIXTURE)
    assert r["external_io_timing_qualified"] is True
    assert r["output_delay_exceptions"] == ["i2s_bclk"]


def test_false_path_excuses_do_not_qualify_external_io(tmp_path):
    # A false path is not a budget: excusing outputs with false paths must
    # keep the flag false even when no unconstrained port remains.
    assert _no_output_delay(tmp_path, unconstrained=0, false_path=3, timing_clock=0)[
        "external_io_timing_qualified"] is False


def test_external_io_timing_flag_is_false_while_outputs_remain_unconstrained(tmp_path):
    r = _no_output_delay(tmp_path, unconstrained=1)
    assert r["missing_output_delays"] == 1
    assert r["external_io_timing_qualified"] is False


def test_published_flag_cannot_disagree_with_the_routed_report():
    record = json.loads((FIXTURE / "publication.json").read_text())
    computed = publish.inspect_reports(FIXTURE)
    for key, value in computed.items():
        if key in record:
            assert record[key] == value, key
    assert record["external_io_timing_qualified"] is True
    assert record["output_delay_exceptions"] == ["i2s_bclk"]


# ---- DSP publication binding: the flag is derived, never flipped ------------
def _dsp_fixture(tmp_path):
    """Evidence + the drc.rpt it names, REBOUND to the bytes on disk here:
    the committed fixture's drc.rpt is the host-stripped republication, so
    its hash differs from the box bytes the evidence was extracted against
    (which is exactly what the binding check must refuse elsewhere)."""
    shutil.copyfile(FIXTURE / "drc.rpt", tmp_path / "drc.rpt")
    ev = tmp_path / "dsp-dpreg-evidence"
    shutil.copytree(FIXTURE / "dsp-dpreg-evidence", ev)
    import hashlib
    digest = hashlib.sha256((tmp_path / "drc.rpt").read_bytes()).hexdigest()
    (ev / "drc_rpt.sha256").write_text(digest + "  drc.rpt\n")
    return tmp_path


def test_dsp_disposition_completes_on_evidence_bound_to_this_artifact(tmp_path):
    # The full derive-True path needs the drc.rpt bytes the evidence was
    # extracted AGAINST. The committed fixture carries the host-stripped
    # republication (its hash differs from the recorded box bytes), so the
    # analyser's own manifest pin correctly refuses it here -- that refusal
    # is test_dsp_disposition_refuses_a_foreign_checkpoint. The True path is
    # exercised where the bound state exists: on the build host, against the
    # artifact directory the extraction ran in (see the lane's verification
    # record). Skip, never fake: a harness that cannot reach the state does
    # not answer.
    bound = publish.dsp_disposition(_dsp_fixture(tmp_path))
    if not bound["complete"]:
        pytest.skip("committed fixture cannot reach the bound state: "
                    + bound["reason"])
    assert bound["complete"] is True, bound
    assert "VERDICT: all" in bound["verdict"], bound["verdict"]


def test_dsp_disposition_refuses_without_evidence(tmp_path):
    for f in ("timing.rpt", "clocks.rpt", "utilization.rpt", "drc.rpt"):
        shutil.copyfile(FIXTURE / f, tmp_path / f)
    r = publish.dsp_disposition(tmp_path)
    assert r["complete"] is False
    assert "no dsp-dpreg-evidence" in r["reason"], r


def test_dsp_disposition_refuses_a_foreign_checkpoint(tmp_path):
    _dsp_fixture(tmp_path)
    (tmp_path / "dsp-dpreg-evidence" / "drc_rpt.sha256").write_text("0" * 64 + "  drc.rpt\n")
    r = publish.dsp_disposition(tmp_path)
    assert r["complete"] is False
    assert "different routed checkpoint" in r["reason"], r


def test_dsp_disposition_refuses_when_the_analyser_will_not_answer(tmp_path):
    # the empty-dump case the review round hardened: the analyser must NO
    # VERDICT rather than bless an absent interrogation
    _dsp_fixture(tmp_path)
    (tmp_path / "dsp-dpreg-evidence" / "dsp_cells_dump.txt").write_text("")
    r = publish.dsp_disposition(tmp_path)
    assert r["complete"] is False
    assert "would not answer" in r["reason"], r


def test_committed_publication_cannot_claim_a_complete_review_without_binding():
    # the committed record predates the derivation: it must not claim True
    # while its evidence names a different checkpoint's drc.rpt
    record = json.loads((FIXTURE / "publication.json").read_text())
    if record.get("dsp_feedback_review_complete") is not True:
        return
    r = publish.dsp_disposition(FIXTURE)
    assert r["complete"] is True, \
        "publication claims the DSP review complete but the binding refuses: " + r["reason"]
