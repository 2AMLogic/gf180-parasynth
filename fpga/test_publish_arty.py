import json
from pathlib import Path
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
#
# dsp_feedback_review_complete is COMPUTED by publish() (dsp_disposition):
# true only when the DPREG-4 evidence names, by sha256, the routed.dcp the
# publication names, carries the complete DRC-derived target set, validates
# against its manifest, and the accepted structural analysis agrees with a
# fresh derivation that finds no reachable P-feedback. Every test below runs
# the PUBLISHER on a self-contained artifact: publish_binding_cases'
# internally consistent copy of the routed fixture (synthetic routed.dcp,
# real routed drc.rpt, real DPREG-4 extraction dump) with the evidence bound
# to it by digest. Nothing here skips: the true path executes in CI.
import hashlib
import importlib.util
import sys

sys.path.insert(0, str(publish.ROOT / "fpga"))
import publish_binding_cases as binding_cases  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "dsp_dpreg_analyse", publish.ROOT / "tools" / "dsp_dpreg_analyse.py")
analyser = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(analyser)

INTEGRATED = publish.ROOT / "fpga/reports/arty/integrated-baseline-2025.1"
_BOUND = ("dsp_cells_dump.txt", "vivado_extract.log", "drc_dpreg_names.txt",
          "drc_rpt.sha256", "routed_dcp.sha256")


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _remanifest(ev):
    (ev / "MANIFEST.sha256").write_text(
        "".join(f"{_sha(ev / n)}  {n}\n" for n in _BOUND if (ev / n).exists()))


def _reanalyse(art):
    """Regenerate the accepted analysis the way the lane does: the analyser
    run against the bound evidence. Returns its exit code (0/2/3)."""
    return analyser.main(["--evidence", str(art / "dsp-dpreg-evidence"),
                          "--drc", str(art / "drc.rpt")])


def _bound_artifact(tmp_path):
    """A publishable artifact whose DSP evidence is bound to ITS routed.dcp
    and drc.rpt by digest, with the accepted analysis derived from it."""
    art = binding_cases.make_artifact(tmp_path)
    record = json.loads((art / "report.json").read_text())
    ev = art / "dsp-dpreg-evidence"
    dcp_path = (ev / "dsp_cells_dump.txt").read_text().splitlines()[1]
    assert dcp_path.startswith("DCP "), dcp_path
    dcp_path = dcp_path[4:]
    (ev / "routed_dcp.sha256").write_text(
        f"{record['artifact_sha256']['routed.dcp']}  {dcp_path}\n")
    (ev / "drc_rpt.sha256").write_text(
        f"{record['artifact_sha256']['drc.rpt']}  "
        f"{dcp_path.rsplit('/', 1)[0]}/drc.rpt\n")
    _remanifest(ev)
    assert _reanalyse(art) == 0
    return art


def _publish(art, tmp_path):
    return publish.publish(art, tmp_path / "publication")


def test_publisher_derives_dsp_review_complete_from_bound_evidence(tmp_path):
    art = _bound_artifact(tmp_path)
    s = _publish(art, tmp_path)
    record = json.loads((art / "report.json").read_text())
    assert s["dsp_feedback_review_complete"] is True, s["dsp_disposition"]
    d = s["dsp_disposition"]
    assert d["reason"] == ""
    assert d["routed_dcp_sha256"] == record["artifact_sha256"]["routed.dcp"]
    assert d["targets"] == s["drc"]["DPREG-4"]["count"] == 13
    assert "VERDICT: all 13" in d["verdict"]
    assert not any("DPREG-4" in r for r in s["remaining_review"])
    # the physical-playback flag is never derived here
    assert s["hardware_playback_tested"] is False


def test_published_output_alone_reproduces_the_dsp_verdict(tmp_path):
    # publish -> REOPEN the published directory -> re-derive: the verdict must
    # be reproducible from the publication alone, not only from the build
    # host's artifact directory (which carries routed.dcp and is not shipped)
    art = _bound_artifact(tmp_path)
    s = _publish(art, tmp_path)
    assert s["dsp_feedback_review_complete"] is True, s["dsp_disposition"]
    out = tmp_path / "publication"
    assert not (out / "routed.dcp").exists()
    reopened = publish.dsp_disposition(out)
    assert reopened == s["dsp_disposition"]
    rec = json.loads((out / "publication.json").read_text())
    assert rec["dsp_disposition"] == reopened
    # the shipped bundle is recorded by digest in the publication
    ev = out / "dsp-dpreg-evidence"
    assert rec["published_evidence_sha256"] == {
        f.name: _sha(f) for f in sorted(ev.iterdir())}


@pytest.mark.parametrize("damage,reason", [
    (lambda ev: __import__("shutil").rmtree(ev),
     "missing evidence: no dsp-dpreg-evidence directory"),
    (lambda ev: (ev / "dsp_cells_dump.txt").write_text(
        (ev / "dsp_cells_dump.txt").read_text() + "\n# tampered\n"),
     "evidence manifest refused: hash mismatch for dsp_cells_dump.txt"),
    (lambda ev: (ev / "routed_dcp.sha256").unlink(),
     "evidence records no routed.dcp digest"),
], ids=["bundle-omitted", "dump-corrupted", "dcp-digest-omitted"])
def test_reopen_refuses_a_damaged_published_bundle(tmp_path, damage, reason):
    art = _bound_artifact(tmp_path)
    assert _publish(art, tmp_path)["dsp_feedback_review_complete"] is True
    out = tmp_path / "publication"
    damage(out / "dsp-dpreg-evidence")
    d = publish.dsp_disposition(out)
    assert d["complete"] is False
    assert reason in d["reason"], d["reason"]


def test_publication_refuses_a_corrupted_evidence_copy(tmp_path, monkeypatch):
    # the copy is integrity-checked after copying: a copy that differs from
    # the artifact's bundle refuses PUBLICATION, it does not ship
    art = _bound_artifact(tmp_path)
    real = publish.shutil.copyfile

    def corrupting(src, dst, *a, **k):
        real(src, dst, *a, **k)
        if Path(src).name == "dsp_cells_dump.txt":
            with open(dst, "ab") as f:
                f.write(b"\n")
        return dst
    monkeypatch.setattr(publish.shutil, "copyfile", corrupting)
    with pytest.raises(ValueError, match="evidence copy differs from the artifact: dsp_cells_dump.txt"):
        _publish(art, tmp_path)


def _drop(name):
    def mutate(art):
        (art / "dsp-dpreg-evidence" / name).unlink()
    return mutate


def _wrong_dcp(art):
    ev = art / "dsp-dpreg-evidence"
    path = (ev / "routed_dcp.sha256").read_text().split(None, 1)[1]
    (ev / "routed_dcp.sha256").write_text("0" * 64 + "  " + path)
    _remanifest(ev)     # a well-formed, manifest-consistent WRONG binding


def _incomplete_targets(art):
    # the accepted analysis covers 12 of the 13 DRC-derived targets
    p = art / "dsp-dpreg-evidence" / "dsp-opmode-analysis.json"
    a = json.loads(p.read_text())
    a["required_instances"] = a["required_instances"][:-1]
    a["cells"] = a["cells"][:-1]
    p.write_text(json.dumps(a, indent=2))


def _reachable_counterexample(art):
    # split OPMODE[4] of the first flagged cell off the net it shares with
    # OPMODE[5]: Z = 010 (P) becomes structurally reachable
    ev = art / "dsp-dpreg-evidence"
    dump = ev / "dsp_cells_dump.txt"
    text = dump.read_text()
    old = ("PIN OPMODE[4] dir=IN net=u_synth/u_voice/osc2_path/p0/pair/dec/prod1 "
           "driver=u_synth/u_voice/osc2_path/p0/pair/dec/prod0__0_i_1/O (LUT5)")
    assert text.count(old) == 1
    dump.write_text(text.replace(
        old, "PIN OPMODE[4] dir=IN net=u_synth/u_voice/osc2_path/p0/pair/dec/split "
             "driver=u_synth/u_voice/osc2_path/p0/pair/dec/split_i/O (LUT5)"))
    _remanifest(ev)
    assert _reanalyse(art) == 2     # the analyser itself finds it


def _stale_analysis(art):
    # accepted analysis says dismissed; the (re-manifested) dump says P is
    # reachable -- the publisher must not consume a stale analysis
    ev = art / "dsp-dpreg-evidence"
    keep = (ev / "dsp-opmode-analysis.json").read_text()
    _reachable_counterexample(art)
    (ev / "dsp-opmode-analysis.json").write_text(keep)


@pytest.mark.parametrize("mutate,reason", [
    (lambda art: __import__("shutil").rmtree(art / "dsp-dpreg-evidence"),
     "missing evidence: no dsp-dpreg-evidence directory"),
    (_drop("dsp_cells_dump.txt"), "missing evidence file(s): ['dsp_cells_dump.txt']"),
    (_drop("dsp-opmode-analysis.json"), "missing evidence file(s): ['dsp-opmode-analysis.json']"),
    (_drop("routed_dcp.sha256"), "evidence records no routed.dcp digest"),
    (_wrong_dcp, "wrong routed checkpoint"),
    (_incomplete_targets, "incomplete target set: accepted analysis covers 12 of 13"),
    (_reachable_counterexample, "reachable P-feedback counterexample"),
    (_stale_analysis, "accepted analysis is not of this dump"),
], ids=["no-evidence-dir", "missing-dump", "missing-analysis", "no-dcp-digest",
        "wrong-dcp", "incomplete-targets", "reachable-counterexample",
        "stale-analysis"])
def test_publisher_refuses_the_dsp_review_for_its_reason(tmp_path, mutate, reason):
    art = _bound_artifact(tmp_path)
    mutate(art)
    s = _publish(art, tmp_path)
    d = s["dsp_disposition"]
    assert s["dsp_feedback_review_complete"] is False
    assert d["complete"] is False
    assert reason in d["reason"], d["reason"]
    # the refusal is carried as data in the review list, with its reason
    assert f"13 DPREG-4 DSP feedback warnings ({d['reason']})" in s["remaining_review"]


def test_counterexample_reason_names_the_cell_and_opmode(tmp_path):
    art = _bound_artifact(tmp_path)
    _reachable_counterexample(art)
    d = _publish(art, tmp_path)["dsp_disposition"]
    assert "osc2_path/p0/pair/dec/prod0__0" in d["reason"], d["reason"]
    assert "OPMODE 010" in d["reason"], d["reason"]


def test_committed_publications_carry_exactly_their_derived_dsp_state():
    # The committed record must say exactly what its own checker derives on
    # the committed files -- never a claim the checker refuses.
    integrated = json.loads((INTEGRATED / "publication.json").read_text())
    derived = publish.dsp_disposition(INTEGRATED)
    assert integrated["dsp_disposition"] == derived
    assert integrated["dsp_feedback_review_complete"] is derived["complete"]
    if not derived["complete"]:
        n = integrated["drc"]["DPREG-4"]["count"]
        assert (f"{n} DPREG-4 DSP feedback warnings ({derived['reason']})"
                in integrated["remaining_review"])
    # and no committed publication anywhere claims a review it cannot derive
    for pub in sorted((publish.ROOT / "fpga/reports/arty").glob("*/publication.json")):
        rec = json.loads(pub.read_text())
        if rec.get("dsp_feedback_review_complete") is True:
            assert publish.dsp_disposition(pub.parent)["complete"] is True, pub


def test_committed_integrated_evidence_is_bound_to_its_routed_dcp_by_digest():
    # Pins the committed integrated baseline. Until 2026-09-25 its extraction
    # named routed.dcp by PATH only and this test pinned the refusal
    # ("evidence records no routed.dcp digest"). A bounded read-only
    # re-extraction (tools/dsp_dpreg_extract.py, box-side digest asserted
    # before and re-checked after Vivado) recorded the digest; the dump it
    # produced is byte-identical to the path-only one (e738fb0c...). If this
    # pin ever fails, the binding has drifted: re-derive, do not edit the pin.
    d = publish.dsp_disposition(INTEGRATED)
    assert d["complete"] is True, d["reason"]
    assert d["routed_dcp_sha256"] == (
        "6c3c22c591671eb1f6790ac0500980f9660b8433ef8a1e235d3da2ee4a21fbf8")
    assert d["targets"] == 13
    assert d["analysis_dump_sha256"].startswith("e738fb0c1b2db9ea")
    rec = (INTEGRATED / "dsp-dpreg-evidence" / "routed_dcp.sha256").read_text()
    assert rec.split() == [d["routed_dcp_sha256"],
                           "/home/ubuntu/integrated-baseline/build/arty/routed.dcp"]
    pub = json.loads((INTEGRATED / "publication.json").read_text())
    # the committed publication ships and hash-records the evidence it cites
    for name, digest in pub["published_evidence_sha256"].items():
        assert _sha(INTEGRATED / "dsp-dpreg-evidence" / name) == digest, name
    assert "routed_dcp.sha256" in pub["published_evidence_sha256"]


def test_extractor_manifest_pins_everything_the_publisher_requires():
    # the extractor's box manifest must cover what dsp_disposition() demands
    # pinned, or a fresh extraction would refuse for an unpinned record
    spec = importlib.util.spec_from_file_location(
        "dsp_dpreg_extract", publish.ROOT / "tools" / "dsp_dpreg_extract.py")
    x = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(x)
    assert set(publish.DSP_MANIFEST_PINNED) <= set(x.MANIFEST_FILES)
