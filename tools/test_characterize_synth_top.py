"""Tests for tools/characterize_synth_top.py (issue #790, T1 item 8).

The behaviour tests run against the evidence this repository actually commits
(`pnr/orfs/evidence/synth_top/joined-d1e5068`), not fixtures invented here --
the distinction `docs/failure-modes.md` is about. The controls (`-k control`,
wired into `make controls`) each inject one shipped class of defect into a
COPY of that evidence and require the tool to REFUSE -- exit 2, message naming
the defect, and NO report written -- never to emit a report with a row
missing. The utilization-target control is the permanent injection of #245:
quoting a die/cell ratio whose die is a target read back.
"""
from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys

import pytest

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import characterize_synth_top as cst  # noqa: E402

EVIDENCE = ROOT / "pnr" / "orfs" / "evidence" / "synth_top" / "joined-d1e5068"
COMMITTED_MD = ROOT / "docs" / "characterization-synth-top.md"
COMMITTED_JSON = ROOT / "docs" / "characterization-synth-top.json"
RUN_SCRIPT = [sys.executable, str(HERE / "characterize_synth_top.py")]


def copy_evidence(tmp_path: pathlib.Path) -> pathlib.Path:
    dest = tmp_path / "joined-d1e5068"
    shutil.copytree(EVIDENCE, dest)
    return dest


def run_tool(run_dir, out_dir):
    return subprocess.run(
        RUN_SCRIPT + [str(run_dir), "--out-dir", str(out_dir)],
        capture_output=True, text=True, cwd=str(ROOT))


def assert_no_report(out_dir: pathlib.Path):
    for name in ("characterization-synth-top.md",
                 "characterization-synth-top.json"):
        assert not (out_dir / name).exists(), \
            f"{name} was written from refused evidence"


@pytest.fixture(scope="session")
def clean_twin():
    return cst.build_report(EVIDENCE, ROOT / "docs")


# ------------------------------------------------------------ the real thing

def test_the_committed_evidence_passes_every_cross_check(clean_twin):
    assert clean_twin["status"] == "pass"
    assert clean_twin["die_area_provenance"]["reason"] == "fixed-die"
    assert clean_twin["run_dir"] == "joined-d1e5068"
    assert {c["check"] for c in clean_twin["cross_checks"] if not c["pass"]} == set()
    assert len(clean_twin["cross_checks"]) >= 9


def test_the_three_corners_match_the_committed_log(clean_twin):
    expected = {
        "tt": (43.592, 0.600, 37.79, 26.5),
        "ss": (10.173, 1.140, 71.21, 14.0),
        "ff": (58.055, 0.363, 23.32, 42.9),
    }
    for corner, (setup, hold, minp, fmax) in expected.items():
        c = clean_twin["per_corner_timing"][corner]
        assert (c["setup_wns_ns"], c["hold_wns_ns"],
                c["implied_min_period_ns"], c["implied_fmax_mhz"]) == \
            (setup, hold, minp, fmax)


def test_power_is_tt_only_and_says_so(clean_twin):
    assert clean_twin["power"]["finish__power__total"]["value"] == 0.143543
    assert any("tt_025C_5v00" in item and "one corner" in item
               for item in clean_twin["not_covered"])
    assert any("no LVS" in item for item in clean_twin["not_covered"])
    assert any("sign-off DRC" in item for item in clean_twin["not_covered"])
    assert any("gate-level simulation" in item
               for item in clean_twin["not_covered"])


def test_the_duplicate_keys_in_6_report_are_carried_as_evidence(clean_twin):
    assert "finish__design__instance__count" in \
        clean_twin["duplicate_json_keys_in_6_report"]
    assert "finish__design__instance__area" in \
        clean_twin["duplicate_json_keys_in_6_report"]
    ambiguous = {a["key"] for a in
                 clean_twin["ambiguous_duplicate_values_in_6_report"]}
    assert ambiguous == {"finish__design__instance__area"}


def test_every_figure_carries_file_and_key_or_line(clean_twin):
    def walk(node):
        if isinstance(node, dict):
            if {"value", "source"} <= set(node) and \
                    isinstance(node.get("source"), dict):
                src = node["source"]
                assert src.get("file"), node
                assert src.get("key") or src.get("line"), node
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(clean_twin)


def test_derived_ratios_carry_the_provenance_gate(clean_twin):
    assert clean_twin["derived_ratios"]["die_over_synth_cell_area"][
        "value"] == 1.75
    assert "quotable only because" in clean_twin["derived_ratios"][
        "die_over_synth_cell_area"]["note"]


# ------------------------------------------------- regeneration is the proof

def test_regeneration_is_byte_identical(tmp_path):
    """The committed docs artifacts are exactly what the tool emits today."""
    result = run_tool(EVIDENCE, tmp_path)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "characterization-synth-top.md").read_bytes() == \
        COMMITTED_MD.read_bytes()
    assert (tmp_path / "characterization-synth-top.json").read_bytes() == \
        COMMITTED_JSON.read_bytes()


def test_the_committed_json_twin_declares_a_verdict():
    """#791: an evidence-shaped JSON must carry an explicit status key."""
    twin = json.loads(COMMITTED_JSON.read_text())
    assert twin["status"] == "pass"
    assert "not a klt signoff verdict" in twin["verdict_scope"]


def test_the_run_dir_is_an_explicit_argument_never_a_glob(tmp_path):
    result = run_tool(ROOT / "pnr" / "orfs" / "evidence" / "synth_top",
                      tmp_path)
    assert result.returncode == 2
    assert "6_report.json" in result.stderr      # parent has no evidence files
    assert_no_report(tmp_path)


# ------------------------------------------------------------------ controls

def test_control_a_deleted_input_file_refuses(tmp_path):
    run_dir = copy_evidence(tmp_path / "a")
    (run_dir / "blockarea.md").unlink()
    out = tmp_path / "outa"
    result = run_tool(run_dir, out)
    assert result.returncode == 2
    assert "blockarea.md" in result.stderr
    assert_no_report(out)


def test_control_a_deleted_required_field_refuses(tmp_path):
    run_dir = copy_evidence(tmp_path / "b")
    report = json.loads((run_dir / "6_report.json").read_text())
    del report["finish__power__total"]
    (run_dir / "6_report.json").write_text(json.dumps(report))
    out = tmp_path / "outb"
    result = run_tool(run_dir, out)
    assert result.returncode == 2
    assert "finish__power__total" in result.stderr
    assert_no_report(out)


def test_control_a_dropped_corner_line_refuses(tmp_path):
    """One corner fewer than the header lists -> a missing row, refused."""
    run_dir = copy_evidence(tmp_path / "c")
    lines = (run_dir / "sta_corners.log").read_text().splitlines()
    kept = [l for l in lines if not l.startswith("=== CORNER ff ")]
    assert len(kept) == len(lines) - 1
    (run_dir / "sta_corners.log").write_text("\n".join(kept) + "\n")
    out = tmp_path / "outc"
    result = run_tool(run_dir, out)
    assert result.returncode == 2
    assert "ff_n40C_5v50" in result.stderr
    assert_no_report(out)


def test_control_a_utilization_target_refuses_the_ratio(tmp_path):
    """#245 as a permanent injection: the exact line ladder_dp ships."""
    run_dir = copy_evidence(tmp_path / "d")
    config = run_dir / "config.mk"
    config.write_text(config.read_text() +
                      "export CORE_UTILIZATION  = 50\n")
    out = tmp_path / "outd"
    result = run_tool(run_dir, out)
    assert result.returncode == 2
    assert "circular-die-area" in result.stderr
    assert_no_report(out)


def test_control_a_differing_duplicate_required_key_refuses(tmp_path):
    """The ambiguity json.load silently resolves by keeping the last."""
    run_dir = copy_evidence(tmp_path / "e")
    text = (run_dir / "6_report.json").read_text()
    # Re-open the root object with an EARLIER copy of a required key whose
    # value differs from the one already there -- json.load would keep the
    # last (the real one) and never tell anyone.
    (run_dir / "6_report.json").write_text(text.replace(
        "{", '{\n\t"finish__design__instance__count": 999999,', 1))
    out = tmp_path / "oute"
    result = run_tool(run_dir, out)
    assert result.returncode == 2
    assert "differing values" in result.stderr
    assert_no_report(out)


# --------------------------------------------------------------- edge cases

def test_a_corner_outside_the_header_list_refuses(tmp_path):
    run_dir = copy_evidence(tmp_path / "f")
    log = run_dir / "sta_corners.log"
    log.write_text(log.read_text().replace(
        "=== DONE",
        "=== CORNER zzz setup_wns_ns=+1.0 setup_tns_ns=0.000 "
        "hold_wns_ns=+1.0 hold_tns_ns=0.000 implied_min_period_ns=80.0 "
        "implied_fmax_mhz=12.5\n=== DONE"))
    result = run_tool(run_dir, tmp_path / "outf")
    assert result.returncode == 2
    assert "not in the header" in result.stderr
    assert_no_report(tmp_path / "outf")


def test_a_negative_wns_is_a_normal_input(tmp_path):
    """None of this evidence has negative slack; the tool must not assume it."""
    lines = EVIDENCE.joinpath("sta_corners.log").read_text().splitlines()
    lines = [l.replace("setup_wns_ns=+43.592", "setup_wns_ns=-2.500")
              .replace("implied_min_period_ns=37.79",
                       "implied_min_period_ns=83.88")
              .replace("implied_fmax_mhz=26.5", "implied_fmax_mhz=11.9")
             for l in lines]
    run_dir = copy_evidence(tmp_path / "g")
    (run_dir / "sta_corners.log").write_text("\n".join(lines) + "\n")
    out = tmp_path / "outg"
    # The tt-vs-finish WNS cross-check must now disagree and refuse: the
    # fixture breaks a real invariant, which is the point of the check.
    result = run_tool(run_dir, out)
    assert result.returncode == 2
    assert "tt setup WNS" in result.stderr
    assert_no_report(out)

    # With the finish-stage figures made consistent with the negative WNS,
    # the negative value flows through and renders with its sign.
    report = json.loads((run_dir / "6_report.json").read_text())
    report["finish__timing__setup__ws"] = -2.5
    report["finish__timing__fmax"] = 11.9e6
    (run_dir / "6_report.json").write_text(json.dumps(report))
    result = run_tool(run_dir, out)
    assert result.returncode == 0, result.stderr
    md = (out / "characterization-synth-top.md").read_text()
    assert "| tt | -2.500" in md


def test_an_fmax_disagreement_beyond_tolerance_refuses(tmp_path):
    run_dir = copy_evidence(tmp_path / "h")
    log = run_dir / "sta_corners.log"
    log.write_text(log.read_text().replace(
        "implied_fmax_mhz=14.0", "implied_fmax_mhz=99.0"))
    result = run_tool(run_dir, tmp_path / "outh")
    assert result.returncode == 2
    assert "implied_fmax" in result.stderr
    assert_no_report(tmp_path / "outh")


def test_min_period_tolerance_boundary(tmp_path):
    """0.004 ns off passes (inside 0.0055); 0.02 off refuses."""
    for drift, expect_ok in ((0.004, True), (0.02, False)):
        run_dir = copy_evidence(tmp_path / ("i" + str(expect_ok)))
        log = run_dir / "sta_corners.log"
        log.write_text(log.read_text().replace(
            "implied_min_period_ns=37.79",
            f"implied_min_period_ns={37.79 + drift:.2f}"))
        result = run_tool(run_dir, tmp_path / ("outi" + str(expect_ok)))
        assert (result.returncode == 0) == expect_ok, result.stderr
        if not expect_ok:
            assert "stated tolerance" in result.stderr


def test_the_tolerances_are_stated_in_the_artifacts(clean_twin):
    assert clean_twin["tolerances"]["min_period_ns"] == 0.0055
    md = cst.render_markdown(clean_twin)
    assert "Cross-checks" in md and "tolerance" in md
