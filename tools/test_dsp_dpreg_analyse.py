#!/usr/bin/env python3
"""Controls for the DPREG-4 analyser (tools/dsp_dpreg_analyse.py).

The analyser dismisses a DPREG-4 warning when no reachable OPMODE value
selects P as an operand. A dismissal checker that could never fire would be
an unsatisfiable gate, so these tests prove both directions:

  * a cell whose OPMODE routing COULD present 010 in the Z field (two
    independent dynamic nets on OPMODE[5] and OPMODE[4]) must be reported
    P-FEEDBACK REACHABLE -- the checker's own red control;
  * a cell whose OPMODE[5] and [4] are the SAME net (the routed reality of
    all 13 flagged instances) must be dismissed.

The refusal controls (docs/failure-modes.md: REFUSED is a first-class
outcome) prove the analyser does NOT answer from absent, truncated,
duplicated or hash-mismatched evidence: every case here was demonstrated
to PASS the pre-fix analyser (see refusal-cases.pre-fix.json next to the
evidence) and must stay refused forever after.

The fixture is a synthetic dump snippet in the dsp_cells_dump.txt format;
no checkpoint or network is needed.
"""

import importlib.util
import json
import pathlib
import subprocess
import sys

import pytest

HERE = pathlib.Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location(
    "dsp_dpreg_analyse", HERE / "dsp_dpreg_analyse.py")
mod = importlib.util.module_from_spec(_spec)
sys.modules["dsp_dpreg_analyse"] = mod
_spec.loader.exec_module(mod)

CELL = """==================================================
==== CELL u_synth/u_voice/test/prod0__0 ====
---- properties ----
PREG = {preg}
SITE = DSP48_X1Y99
---- pins ----
{pins}
---- P/PCOUT fanout ----
FANOUT P[0] net test/n_0 loads=1
  load test/ff/D (FDRE)
---- control cones (OPMODE/ALUMODE/CARRYINSEL/CARRYIN/CE/RST) ----
CONE test/prod0__0 END nets=0
==== END CELL u_synth/u_voice/test/prod0__0 ====
"""


def dump(preg, pins):
    return f"TOTAL_DSP48E1 1\n" + CELL.format(preg=preg, pins=pins)


OPMODE_PINS = """PIN OPMODE[6] dir=IN net=test/<const0> driver=u_synth/test/GND/G (GND)
PIN OPMODE[5] dir=IN net=test/{net5} driver={drv5}
PIN OPMODE[4] dir=IN net=test/{net4} driver={drv4}
PIN OPMODE[3] dir=IN net=test/<const0> driver=u_synth/test/GND/G (GND)
PIN OPMODE[2] dir=IN net=test/<const1> driver=u_synth/test/VCC/P (VCC)
PIN OPMODE[1] dir=IN net=test/<const0> driver=u_synth/test/GND/G (GND)
PIN OPMODE[0] dir=IN net=test/<const1> driver=u_synth/test/VCC/P (VCC)"""


def test_same_net_pairing_is_dismissed():
    pins = OPMODE_PINS.format(net5="q", drv5="u_synth/test/lut/O (LUT5)",
                              net4="q", drv4="u_synth/test/lut/O (LUT5)")
    cells = mod.parse_cells(dump("0", pins))
    rec = mod.analyse("u_synth/u_voice/test/prod0__0",
                      cells["u_synth/u_voice/test/prod0__0"])
    assert not rec["p_feedback_reachable"]
    zs = {v["z"] for v in rec["reachable_opmode_values"]}
    assert zs == {"0", "C"}  # 000 and 011 only -- never P


def test_separate_nets_make_p_reachable():
    pins = OPMODE_PINS.format(net5="qa", drv5="u_synth/test/lut/O (LUT5)",
                              net4="qb", drv4="u_synth/test/lut2/O (LUT5)")
    cells = mod.parse_cells(dump("0", pins))
    rec = mod.analyse("u_synth/u_voice/test/prod0__0",
                      cells["u_synth/u_voice/test/prod0__0"])
    assert rec["p_feedback_reachable"]  # the checker CAN fire
    assert any(v["z"] == "P" for v in rec["reachable_opmode_values"])


def test_preg_one_still_reports_mux_truth():
    # PREG is not part of the reachability verdict; record that the property
    # is carried through so the document table stays honest.
    pins = OPMODE_PINS.format(net5="q", drv5="u_synth/test/lut/O (LUT5)",
                              net4="q", drv4="u_synth/test/lut/O (LUT5)")
    cells = mod.parse_cells(dump("1", pins))
    rec = mod.analyse("u_synth/u_voice/test/prod0__0",
                      cells["u_synth/u_voice/test/prod0__0"])
    assert rec["PREG"] == "1"
    assert not rec["p_feedback_reachable"]


# ------------------------------------------------- refusal controls (red)

def strip_opmode_pins(block):
    return "\n".join(l for l in block.splitlines()
                     if not l.startswith("PIN OPMODE")) + "\n"


CELL_NAME = "u_synth/u_voice/test/prod0__0"


def full_cell_block(preg="0", pins=None):
    if pins is None:
        pins = OPMODE_PINS.format(net5="q", drv5="u_synth/test/lut/O (LUT5)",
                                  net4="q", drv4="u_synth/test/lut/O (LUT5)")
    return CELL.format(preg=preg, pins=pins)


def test_duplicate_cell_header_is_refused():
    d = dump("0", OPMODE_PINS.format(net5="q", drv5="d/O (LUT5)",
                                     net4="q", drv4="d/O (LUT5)"))
    with pytest.raises(mod.Refused, match="duplicate cell header"):
        mod.parse_cells(d + full_cell_block())


def test_opmodeless_cell_is_a_defect_not_mode_zero():
    block = strip_opmode_pins(full_cell_block())
    cells = mod.parse_cells("TOTAL_DSP48E1 1\n" + block)
    with pytest.raises(mod.Refused, match="no OPMODE pins"):
        mod.analyse(CELL_NAME, cells[CELL_NAME])


def test_partial_opmode_pins_are_refused():
    pins = "\n".join(OPMODE_PINS.format(net5="q", drv5="d/O (LUT5)",
                                        net4="q", drv4="d/O (LUT5)")
                     .splitlines()[:5])  # bits 6..2 only
    cells = mod.parse_cells("TOTAL_DSP48E1 1\n" + full_cell_block(pins=pins))
    with pytest.raises(mod.Refused, match="all seven bits"):
        mod.validate_required_cell(CELL_NAME, cells[CELL_NAME])


def test_missing_mandatory_property_is_refused():
    block = full_cell_block().replace("SITE = DSP48_X1Y99\n", "")
    cells = mod.parse_cells("TOTAL_DSP48E1 1\n" + block)
    with pytest.raises(mod.Refused, match="mandatory properties"):
        mod.validate_required_cell(CELL_NAME, cells[CELL_NAME])


DRC_SNIPPET = """
blah
| DPREG-4 | Warning  | DSP48E1_PregDynOpmodeZmuxP: | 2      |
DPREG-4#1 Warning
DSP48E1_PregDynOpmodeZmuxP:  
The DSP48E1 cell u_synth/u_voice/a/prod0__0 with the given dynamic OPMODE[6:0] connections may lead
Related violations: <none>

DPREG-4#2 Warning
DSP48E1_PregDynOpmodeZmuxP:  
The DSP48E1 cell u_synth/u_voice/b/p_1_out with the given dynamic OPMODE[6:0] connections may lead
Related violations: <none>
"""


def test_drc_targets_derived_not_hardcoded():
    names = mod.parse_drc_targets(DRC_SNIPPET)
    assert names == ["u_synth/u_voice/a/prod0__0",
                     "u_synth/u_voice/b/p_1_out"]


def test_drc_table_count_mismatch_is_refused():
    with pytest.raises(mod.Refused, match="summary says"):
        mod.parse_drc_targets(DRC_SNIPPET.replace("| 2      |", "| 13     |"))


def test_drc_without_dpreg_row_is_refused():
    with pytest.raises(mod.Refused, match="no DPREG-4"):
        mod.parse_drc_targets("no violations here\n")


def test_manifest_hash_mismatch_is_refused(tmp_path):
    (tmp_path / "dsp_cells_dump.txt").write_text("x")
    (tmp_path / "MANIFEST.sha256").write_text(
        "0" * 64 + "  dsp_cells_dump.txt\n")
    with pytest.raises(mod.Refused, match="hash mismatch"):
        mod.verify_manifest(tmp_path)


def test_missing_manifest_is_refused(tmp_path):
    with pytest.raises(mod.Refused, match="missing evidence manifest"):
        mod.verify_manifest(tmp_path)


def test_missing_listed_input_is_refused(tmp_path):
    (tmp_path / "MANIFEST.sha256").write_text(
        "0" * 64 + "  dsp_cells_dump.txt\n")
    with pytest.raises(mod.Refused, match="missing input"):
        mod.verify_manifest(tmp_path)


def test_drc_identity_drift_is_refused(tmp_path):
    (tmp_path / "drc.rpt").write_text("line\nDPREG-4#1 Warning\n")
    (tmp_path / "drc_dpreg_names.txt").write_text("1:DPREG-4#2 Warning\n")
    with pytest.raises(mod.Refused, match="identity drift"):
        mod.verify_drc_identity(tmp_path, tmp_path / "drc.rpt")


def test_missing_drc_report_is_refused(tmp_path):
    (tmp_path / "drc_dpreg_names.txt").write_text("1:DPREG-4#1 Warning\n")
    with pytest.raises(mod.Refused, match="missing bound DRC report"):
        mod.verify_drc_identity(tmp_path, tmp_path / "drc.rpt")


def test_extra_dynamic_cell_is_refused():
    cells = mod.parse_cells("TOTAL_DSP48E1 1\n" + full_cell_block()
                            + """==== CELL u_synth/u_voice/ghost/p_1_out__9 ====
---- properties ----
PREG = 0
SITE = DSP48_X1Y98
---- pins ----
PIN OPMODE[5] dir=IN net=qb driver=d/O (LUT5)
PIN OPMODE[4] dir=IN net=qb2 driver=d2/O (LUT5)
==== END CELL u_synth/u_voice/ghost/p_1_out__9 ====
""")
    with pytest.raises(mod.Refused, match="extra dump cell"):
        mod.refuse_extra_dynamic_cells(
            cells, ["u_synth/u_voice/test/prod0__0"])


def test_constant_opmode_sibling_is_allowed():
    cells = mod.parse_cells("TOTAL_DSP48E1 1\n" + full_cell_block()
                            + """==== CELL u_synth/u_voice/test/acc0 ====
---- properties ----
PREG = 0
SITE = DSP48_X1Y97
---- pins ----
PIN OPMODE[6] dir=IN net=test/<const1> driver=d/VCC/P (VCC)
PIN OPMODE[5] dir=IN net=test/<const0> driver=d/GND/G (GND)
PIN OPMODE[4] dir=IN net=test/<const0> driver=d/GND/G (GND)
PIN OPMODE[3] dir=IN net=test/<const0> driver=d/GND/G (GND)
PIN OPMODE[2] dir=IN net=test/<const0> driver=d/GND/G (GND)
PIN OPMODE[1] dir=IN net=test/<const0> driver=d/GND/G (GND)
PIN OPMODE[0] dir=IN net=test/<const1> driver=d/VCC/P (VCC)
==== END CELL u_synth/u_voice/test/acc0 ====
""")
    mod.refuse_extra_dynamic_cells(
        cells, ["u_synth/u_voice/test/prod0__0"])  # must not raise


# --------------------------------- end-to-end against the real evidence

def test_real_committed_evidence_still_dismisses_13_of_13():
    """The analyser on the preserved extraction + manifest must still
    reproduce the 13/13 dismissal exactly (PR #200's evidence)."""
    r = subprocess.run([sys.executable, str(HERE / "dsp_dpreg_analyse.py")],
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr
    assert ("VERDICT: all 13 cells: P-feedback unreachable on every "
            "reachable OPMODE") in r.stdout
    analysis = json.loads((HERE.parent / (
        "fpga/reports/arty/vivado-2025.1/dsp-dpreg-evidence/"
        "dsp-opmode-analysis.json")).read_text())
    assert analysis["required_source"].startswith("drc.rpt")
    assert len(analysis["required_instances"]) == 13
    assert len(analysis["cells"]) == 13


# ------------------------- extractor: the routed.dcp digest is recorded
# The integrated baseline's first extraction named its checkpoint by PATH
# only, so fpga/publish_arty.dsp_disposition() correctly refused to bind it
# to the routed.dcp the publication names. The extractor now records the
# digest itself (routed_dcp.sha256, box-side, manifest-pinned), re-hashes
# the checkpoint after Vivado closes it, and refuses a dump that opened a
# different path. These run the generated box scripts locally on a fake
# checkpoint; no network or Vivado is needed.

_xspec = importlib.util.spec_from_file_location(
    "dsp_dpreg_extract", HERE / "dsp_dpreg_extract.py")
xmod = importlib.util.module_from_spec(_xspec)
_xspec.loader.exec_module(xmod)


def _fake_box(tmp_path):
    dcp = tmp_path / "build" / "routed.dcp"
    dcp.parent.mkdir()
    dcp.write_bytes(b"not really a checkpoint\n")
    drc = dcp.parent / "drc.rpt"
    drc.write_text("| DPREG-4 | Warning | x | 1 |\n")
    import hashlib
    return dcp, drc, hashlib.sha256(dcp.read_bytes()).hexdigest()


def _bash(script):
    return subprocess.run(["bash", "-s"], input=script, text=True,
                          capture_output=True)


def test_extractor_stage_records_the_dcp_digest_and_path(tmp_path):
    dcp, drc, sha = _fake_box(tmp_path)
    remote = tmp_path / "session"
    r = _bash(xmod.stage_script(str(dcp), sha, str(drc), str(remote)))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "PRECONDITIONS_OK" in r.stdout
    rec = (remote / "routed_dcp.sha256").read_text()
    # the exact shape publish_arty.dsp_disposition() parses
    words = rec.split()
    assert words == [sha, str(dcp)], rec
    assert "routed_dcp.sha256" in xmod.MANIFEST_FILES


def test_extractor_stage_refuses_a_wrong_dcp_and_records_nothing(tmp_path):
    dcp, drc, sha = _fake_box(tmp_path)
    remote = tmp_path / "session"
    r = _bash(xmod.stage_script(str(dcp), "0" * 64, str(drc), str(remote)))
    assert r.returncode == 42, r.stdout + r.stderr
    assert "REFUSED: DCP hash mismatch" in r.stdout
    assert not (remote / "routed_dcp.sha256").exists()


def test_extractor_recheck_refuses_a_checkpoint_changed_by_the_run(tmp_path):
    dcp, _, sha = _fake_box(tmp_path)
    assert _bash(xmod.recheck_script(str(dcp), sha)).returncode == 0
    dcp.write_bytes(b"rewritten by a write_checkpoint\n")
    r = _bash(xmod.recheck_script(str(dcp), sha))
    assert r.returncode == 44, r.stdout + r.stderr
    assert "REFUSED: DCP changed during extraction" in r.stdout


def _pulled(tmp_path, sha, rec_path, dump_path):
    ev = tmp_path / "ev"
    ev.mkdir()
    (ev / "routed_dcp.sha256").write_text(f"{sha}  {rec_path}\n")
    (ev / "dsp_cells_dump.txt").write_text(
        f"VIVADO 2025.1\nDCP {dump_path}\nTOTAL_DSP48E1 0\n")
    return ev


def test_extractor_binding_accepts_the_digest_and_path_it_asserted(tmp_path):
    ev = _pulled(tmp_path, "a" * 64, "/b/routed.dcp", "/b/routed.dcp")
    assert xmod.verify_dcp_binding(ev, "/b/routed.dcp", "a" * 64) == "a" * 64


@pytest.mark.parametrize("sha,rec_path,dump_path,why", [
    ("b" * 64, "/b/routed.dcp", "/b/routed.dcp", "digest"),
    ("a" * 64, "/old/routed.dcp", "/b/routed.dcp", "names"),
    ("a" * 64, "/b/routed.dcp", "/old/routed.dcp", "dump opened"),
])
def test_extractor_binding_refuses_drift(tmp_path, sha, rec_path, dump_path,
                                         why):
    ev = _pulled(tmp_path, sha, rec_path, dump_path)
    with pytest.raises(SystemExit) as exc:
        xmod.verify_dcp_binding(ev, "/b/routed.dcp", "a" * 64)
    assert str(exc.value).startswith("REFUSED:") and why in str(exc.value)


def test_extractor_binding_refuses_a_missing_digest_record(tmp_path):
    ev = _pulled(tmp_path, "a" * 64, "/b/routed.dcp", "/b/routed.dcp")
    (ev / "routed_dcp.sha256").unlink()
    with pytest.raises(SystemExit, match="REFUSED: no routed_dcp.sha256"):
        xmod.verify_dcp_binding(ev, "/b/routed.dcp", "a" * 64)


def test_extractor_will_not_overwrite_a_different_local_drc(tmp_path):
    # the fetched box drc.rpt lands at <evidence>/../drc.rpt; pointed at a
    # committed publication that would silently replace its host-scrubbed
    # report with the box's bytes
    dst = tmp_path / "drc.rpt"
    dst.write_text("| Host : omitted from public report\n")
    with pytest.raises(SystemExit, match="REFUSED: .*drc.rpt"):
        xmod.write_fetched_drc(dst, "| Host : buildbox\n")
    xmod.write_fetched_drc(dst, dst.read_text())  # identical bytes: fine
    new = tmp_path / "fresh" / "drc.rpt"
    new.parent.mkdir()
    xmod.write_fetched_drc(new, "x\n")
    assert new.read_text() == "x\n"
