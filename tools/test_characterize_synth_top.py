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
    """Both sides of MIN_PERIOD_TOLERANCE_NS, at two levels.

    Function level (the cst._check calls below): a fixed 0.004 ns drift is
    accepted -- a sub-granularity value the committed evidence cannot
    express through file injection, because `implied_min_period_ns` is
    logged at 2 dp so the smallest injectable drift is 0.01 ns, already
    larger than the 0.0055 ns tolerance. That is why the inside-boundary
    arm lives here rather than at integration level, and why the drift is
    the FIXED value 0.004 rather than anything derived from the constant:
    this test fails if MIN_PERIOD_TOLERANCE_NS is ever reduced below
    0.004 ns.

    Integration level (the file-injection arm below): the committed
    evidence with a 0.02 ns drift (37.79 -> 37.81, which does modify the
    file) must refuse through the full tool path.

    The 0.004 pass arm this test used to run at integration level injected
    nothing: `f"{37.79 + 0.004:.2f}"` is `"37.79"`, byte-identical to the
    target, so it re-ran clean evidence and asserted nothing beyond the
    clean path (issue #834). The changed-file guard on the injection arm
    below exists so that class cannot come back. Its known limit
    (docs/verification-rules.md rule 8): an injection that changes the file
    but at a line the asserted check never reads -- e.g. editing a corner
    other than the one the refusal names -- still passes this guard.
    """
    # The tolerance is the derivation, not a number that drifted from it.
    assert cst.MIN_PERIOD_TOLERANCE_NS == \
        cst.MIN_PERIOD_HALF_ULP_NS + cst.WNS_TOLERANCE_NS

    # Function level, inside side: fixed 0.004 ns, accepted.
    cst._check([], "boundary inside", 0.0, 0.004,
               cst.MIN_PERIOD_TOLERANCE_NS, "fixed 0.004 ns drift", [])

    # Function level, exact boundary: diff == tol passes (the check refuses
    # only beyond tol + 1e-12); one least-count above refuses.
    cst._check([], "boundary exact", 0.0, cst.MIN_PERIOD_TOLERANCE_NS,
               cst.MIN_PERIOD_TOLERANCE_NS, "diff == tol passes", [])
    with pytest.raises(cst.Refused, match="boundary just outside"):
        cst._check([], "boundary just outside", 0.0,
                   cst.MIN_PERIOD_TOLERANCE_NS + 1e-9,
                   cst.MIN_PERIOD_TOLERANCE_NS, "diff beyond tol", [])

    # Integration level, outside side: 0.02 ns off refuses, and the
    # injection is asserted to have changed the file -- a no-op injection
    # would re-assert clean evidence (the #834 defect).
    run_dir = copy_evidence(tmp_path / "i-refuse")
    log = run_dir / "sta_corners.log"
    original = log.read_text()
    modified = original.replace(
        "implied_min_period_ns=37.79",
        f"implied_min_period_ns={37.79 + 0.02:.2f}")
    assert modified != original, (
        "injection was a no-op: replacement identical to the target, so "
        "this arm would run clean evidence and assert nothing")
    log.write_text(modified)
    result = run_tool(run_dir, tmp_path / "outi-refuse")
    assert result.returncode == 2, result.stderr
    assert "stated tolerance" in result.stderr
    assert_no_report(tmp_path / "outi-refuse")


def test_per_corner_fmax_budget_tracks_the_min_period_rounding():
    """The per-corner Fmax budget is T-dependent, not a widened constant.

    #834 item 1. At the committed tt period the propagated min-period term
    is 0.0035 MHz (budget 0.0535); at T = 5 ns it is 0.2 MHz, above the
    whole flat 1-dp half-ULP. The expected values here are computed from
    the issue's derivation independently of the tool's own formula. Just
    outside the committed-T budget must still refuse, so the widening does
    not become an acceptance of real disagreement.
    """
    assert cst.per_corner_fmax_tolerance_mhz(37.79) == pytest.approx(
        0.0535, abs=5e-4)
    assert cst.per_corner_fmax_tolerance_mhz(5.0) == pytest.approx(
        0.25, abs=5e-4)
    assert cst.per_corner_fmax_tolerance_mhz(5.0) > cst.FMAX_TOLERANCE_MHZ
    assert cst.per_corner_fmax_tolerance_mhz(5.0) > \
        cst.per_corner_fmax_tolerance_mhz(37.79)
    # Just outside the committed-T budget refuses.
    budget = cst.per_corner_fmax_tolerance_mhz(37.79)
    with pytest.raises(cst.Refused, match="committed-T just outside"):
        cst._check([], "committed-T just outside",
                   1000.0 / 37.79, 1000.0 / 37.79 + budget + 1e-9, budget,
                   "diff one epsilon beyond the budget", [])


def test_arithmetically_consistent_fast_design_is_accepted(tmp_path):
    """#834's predicted regression: T = 5 ns evidence must not be refused.

    A producer logging a true period of 5.004 ns emits
    implied_min_period_ns=5.00 (2 dp) and implied_fmax_mhz=199.8 (1 dp,
    from the true reciprocal 199.840). 1000/5.00 = 200.0 differs from the
    logged 199.8 by 0.2 MHz -- four times the flat half-ULP, yet pure
    rounding of consistent evidence. Under the old flat tolerance this
    refused (exit 2); the per-corner budget 0.05 + 1000/25 * 0.005 = 0.25
    accepts it.
    """
    run_dir = copy_evidence(tmp_path / "fast")
    log = run_dir / "sta_corners.log"
    original = log.read_text()
    modified = original.replace(
        "setup_wns_ns=+43.592", "setup_wns_ns=+76.380"
    ).replace(
        "implied_min_period_ns=37.79", "implied_min_period_ns=5.00"
    ).replace(
        "implied_fmax_mhz=26.5", "implied_fmax_mhz=199.8")
    assert modified != original, (
        "injection was a no-op: replacement identical to the target")
    log.write_text(modified)
    report = json.loads((run_dir / "6_report.json").read_text())
    report["finish__timing__setup__ws"] = 76.38
    report["finish__timing__fmax"] = 1.9984e8
    (run_dir / "6_report.json").write_text(json.dumps(report))
    out = tmp_path / "out-fast"
    result = run_tool(run_dir, out)
    assert result.returncode == 0, result.stderr


def test_the_committed_T_budget_is_wider_than_the_flat_half_ULP(tmp_path):
    """The widening is exercised, not just derived.

    An ff drift the flat 0.05 refuses (|1000/23.32 - 42.83| = 0.0516)
    sits inside the per-corner budget 0.0592 and must now pass. Changing
    ff's Fmax touches only the per-corner check: the finish-stage Fmax
    check reads tt.
    """
    assert abs(1000.0 / 23.32 - 42.83) > cst.FMAX_TOLERANCE_MHZ
    assert abs(1000.0 / 23.32 - 42.83) <= \
        cst.per_corner_fmax_tolerance_mhz(23.32)
    run_dir = copy_evidence(tmp_path / "ff")
    log = run_dir / "sta_corners.log"
    original = log.read_text()
    modified = original.replace(
        "implied_fmax_mhz=42.9", "implied_fmax_mhz=42.83")
    assert modified != original, (
        "injection was a no-op: replacement identical to the target")
    log.write_text(modified)
    out = tmp_path / "out-ff"
    result = run_tool(run_dir, out)
    assert result.returncode == 0, result.stderr


def test_the_tolerances_are_stated_in_the_artifacts(clean_twin):
    assert clean_twin["tolerances"]["min_period_ns"] == 0.0055
    md = cst.render_markdown(clean_twin)
    assert "Cross-checks" in md and "tolerance" in md
    tol_by_name = {c["check"]: c["tolerance"]
                   for c in clean_twin["cross_checks"]}
    # The finish-stage Fmax check keeps the FLAT 1-dp half-ULP: its other
    # operand, finish__timing__fmax, is a full-precision Hz float, so that
    # half-ULP is the whole budget. It must not grow the propagated term
    # -- there is no second rounding source for it to absorb (#834).
    assert tol_by_name["tt implied Fmax vs finish-stage fmax"] == \
        cst.FMAX_TOLERANCE_MHZ
    # Each per-corner budget exceeds the flat half-ULP by its own
    # propagated term, recorded in the twin at full precision.
    for corner in ("tt", "ss", "ff"):
        t = clean_twin["per_corner_timing"][corner]["implied_min_period_ns"]
        name = f"{corner}: 1000/implied_min_period vs implied_fmax"
        assert tol_by_name[name] == \
            pytest.approx(cst.per_corner_fmax_tolerance_mhz(t))
        assert tol_by_name[name] > cst.FMAX_TOLERANCE_MHZ
