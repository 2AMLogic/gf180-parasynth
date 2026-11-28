#!/usr/bin/env python3
"""Tests for `tools/cymbal_vca_drive.py` -- the swing-VCA drive read, SN p.13.

Three kinds of test, in the order they carry weight:

  1. KNOWN ANSWERS THAT DO NOT COME FROM THIS MODULE. The band-pass peak gains
     must reproduce W14b Figure 4's digitised values (`model/cymbal_candidate
     .BP_PEAK_DB`), and the f0/Q of both band-passes and of Hh1 must reproduce
     reference §10. Nothing about Figure 4 or §10 informed the component values
     read here, and in particular nothing anywhere in this repository carried
     the band-passes' INPUT networks before this module did -- so the peak-gain
     agreement is an external check on the part of the read that is new.
  2. CONTROLS THAT MUST FAIL. Seven injected defects, each one a change to a
     component value or a wire, each required to turn at least one property
     red; four blindness assertions, each a property that must NOT move under
     a defect it has no business seeing. A known answer nobody has seen fail is
     not a control.
  3. REFUSALS. The pinned scan, and the committed balance record this module's
     headline is quoted against -- each refusing from `main()` with the declared
     REFUSED status, never with a status a runner reads as a measured FAIL (#1789).
     And the clean run itself: when IT refuses, no control has run, so the gate
     is NO EVIDENCE from `--check` and from `--json` alike, never a traceback
     wearing exit 1 (#2056).
"""

from __future__ import annotations

import json
import math
import pathlib
import re
import sys

import numpy as np
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for p in (ROOT / "tools", ROOT / "model"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import cymbal_candidate as cc          # noqa: E402
import cymbal_vca_drive as vd          # noqa: E402
import exit_contract                   # noqa: E402
import tone_stage_schematic as ts      # noqa: E402
import werner_fig4 as wf               # noqa: E402
from exit_contract import PASS, REFUSED  # noqa: E402

GRID = np.geomspace(300.0, 30000.0, 601)


# ---------------------------------------------------------------------------
# 1. Known answers that do not come from this module
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("which", ["low", "high"])
def test_peak_gain_reproduces_werner_figure_4(which):
    """The external known answer, and the only test of the input networks.

    C10/R52 and C11/R55 appear in no other document here. They set the
    filters' absolute gain and nothing else in the read does. Figure 4 was
    digitised from a different artifact by a different author with no
    knowledge of them.
    """
    _, db = vd.bandpass_peak(which)
    assert abs(db - cc.BP_PEAK_DB[which]) <= vd.PEAK_TOL_DB, (
        f"{which}: schematic {db:.3f} dB vs Figure 4 {cc.BP_PEAK_DB[which]:.2f} dB")


@pytest.mark.parametrize("which,f0", [("low", 3450.0), ("high", 7100.0)])
def test_f0_reproduces_reference_section_10(which, f0):
    got, _ = vd.bandpass_f0_q(which)
    assert abs(got - f0) / f0 <= vd.F0_TOL, f"{which}: {got:.1f} Hz vs {f0} Hz"


@pytest.mark.parametrize("which", ["low", "high"])
def test_q_reproduces_reference_section_10(which):
    _, q = vd.bandpass_f0_q(which)
    assert abs(q - 6.0) / 6.0 <= vd.Q_TOL, f"{which}: Q {q:.3f} vs 6"


def test_hh1_reproduces_reference_section_10():
    """Hh1 is the network the upper-bound convention depends on, so its own
    values get a known answer rather than being trusted because §10 has them."""
    f0, q = vd.hh1_f0_q()
    assert abs(f0 - 2500.0) / 2500.0 <= vd.F0_TOL, f0
    assert abs(q - 0.97) / 0.97 <= vd.Q_TOL, q


def test_the_two_band_pass_formulations_agree():
    """A hand-eliminated expression and an explicit (G + sC) v = b solve.

    Two formulations where one would do, for the same reason
    `tone_stage_schematic` carries two: the hand elimination is the one a
    reader can check, and the MNA build is the one that is hard to get subtly
    wrong. Agreement makes the hand step a checked one.
    """
    for which in ("low", "high"):
        a = vd._db(vd.bandpass_response(which, GRID))
        b = vd._db(vd.bandpass_response_mna(which, GRID))
        assert float(np.max(np.abs(a - b))) < 1e-9, which


def test_control_the_formulations_would_notice_a_swapped_component():
    """...and that agreement is not vacuous.

    Perturb the shunt resistor inside ONE formulation only. If the two builds
    were secretly the same code path the difference would stay at 1e-14.
    """
    cfg = vd.config()
    bad = {"stages": cfg["stages"], "hh1": cfg["hh1"], "defect": None,
           "bandpass": {k: dict(v) for k, v in cfg["bandpass"].items()}}
    bad["bandpass"]["low"]["shunt_ohm"] = 5600.0
    a = vd._db(vd.bandpass_response("low", GRID, cfg))
    b = vd._db(vd.bandpass_response_mna("low", GRID, bad))
    assert float(np.max(np.abs(a - b))) > 5.0


# ---------------------------------------------------------------------------
# 2. The structural finding
# ---------------------------------------------------------------------------


def test_the_three_input_stages_are_component_identical():
    st = vd.config()["stages"]
    for field in ("couple_f", "bias_ohm", "emitter_ohm"):
        vals = {b: st[b][field] for b in vd.BANDS}
        assert len(set(vals.values())) == 1, f"{field}: {vals}"


def test_the_two_high_bands_hang_on_one_node():
    st = vd.config()["stages"]
    assert st["short"]["feed"] == st["decay"]["feed"] == "IC3 pin 7"
    assert st["low"]["feed"] == "IC3 pin 1"


def test_only_the_collector_load_differs():
    diff = vd.stage_differences()
    assert diff["differing"] == ["load_ohm"], diff


def test_the_base_bias_is_a_series_pair_not_a_divider():
    """Load-bearing, and it is the thing I first read wrong.

    A 1M/1M divider to ground would fix each base's VOLTAGE, and the three
    stages' collector currents would then depend on Vbe spread. Two 1M in
    SERIES from B1 with no ground leg fixes each base's CURRENT, so all three
    stages carry the same Ic and the same gm, and the gain ratio is exactly
    the collector-load ratio. The read is 2 MΩ, not 500 kΩ.
    """
    for b in vd.BANDS:
        st = vd.STAGES[b]
        assert st["bias_ohm"] == pytest.approx(2.0e6)
        assert len(st["bias"]) == 2 and all("1M" in x for x in st["bias"])


def test_the_vca_term_is_exactly_the_collector_load_ratio():
    term = vd.vca_term_db()
    assert term["chain_db"]["short"] == pytest.approx(20 * math.log10(39 / 22), abs=1e-3)
    assert term["chain_db"]["decay"] == pytest.approx(20 * math.log10(33 / 22), abs=1e-3)
    assert term["chain_db"]["low"] == pytest.approx(0.0, abs=1e-9)


def test_the_loaded_convention_is_an_upper_bound_on_the_unloaded_one():
    """Loading the low band and not the high bands can only RAISE the ratio.

    That is the direction that matters: the headline is that the VCA term is
    too SMALL to close the gap, so it must be argued against the largest
    admissible value, not the most likely one.
    """
    term = vd.vca_term_db()
    for b in ("short", "decay"):
        assert term["bound_db"][b] > term["chain_db"][b]


def test_the_short_band_would_need_a_collector_load_the_schematic_does_not_print():
    need = vd.required_collector_load()
    assert need["short"]["printed_ohm"] == 39e3
    assert need["short"]["required_ohm"] == pytest.approx(2.15e6, rel=0.01)
    assert need["short"]["shortfall_db"] > 30.0


def test_the_decay_bands_gap_is_the_one_the_vca_nearly_reaches():
    """Reported because it is the half that does NOT support the headline.

    The DECAY band's gap is 10.13 dB and the upper bound supplies 7.26 dB. The
    conclusion "the VCA section is not the missing factor" is carried by the
    SHORT band alone, and saying so is the difference between a finding and an
    overstatement.
    """
    term = vd.vca_term_db()
    gap = vd.balance_targets()["gap_db"]
    assert gap["decay"] - term["bound_db"]["decay"] < 3.0
    assert gap["short"] - term["bound_db"]["short"] > vd.GAP_MARGIN_DB


# ---------------------------------------------------------------------------
# 3. Controls that must fail, and blindness
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("defect", vd.DEFECTS)
def test_every_injected_defect_turns_a_property_red(defect):
    targets = vd.balance_targets()
    got = vd.properties(vd.config(defect), targets)
    red = [n for n, p in got.items() if not p["ok"]]
    assert red, f"{defect} turned nothing red"


@pytest.mark.parametrize("defect,blind", sorted(vd.BLIND.items()))
def test_asserted_blindness_is_verified_blind(defect, blind):
    targets = vd.balance_targets()
    got = vd.properties(vd.config(defect), targets)
    moved = [n for n in blind if not got[n]["ok"]]
    assert not moved, f"{defect} moved {moved}, which was asserted blind"


def test_the_input_network_is_load_bearing_and_only_the_peak_sees_it():
    """The paired positive for the blindness assertion above.

    `DROP_INPUT_NETWORK` must move the peak gain by a lot -- otherwise the
    external known answer is not testing the input read at all, and the
    blindness of f0/Q is a statement about nothing.
    """
    base = vd.bandpass_peak("high")[1]
    hurt = vd.bandpass_peak("high", vd.config("DROP_INPUT_NETWORK"))[1]
    assert abs(hurt - base) > 5.0, (base, hurt)


def test_every_defect_is_a_component_change_not_a_bound_change():
    """A defect that relaxed a bound would make the control vacuous."""
    base = vd.config()
    for d in vd.DEFECTS:
        cfg = vd.config(d)
        assert set(cfg) == set(base)
        assert cfg["defect"] == d
    assert vd.PEAK_TOL_DB == 0.05 and vd.GAP_MARGIN_DB == 20.0


def test_an_unknown_defect_refuses():
    with pytest.raises(vd.Refused):
        vd.config("NOT_A_DEFECT")


def test_the_gate_is_green_on_the_committed_read():
    ok, lines, refused = vd.check()
    assert ok and not refused, "\n".join(lines)
    assert not any("REFUSED" in ln for ln in lines), "\n".join(lines)


# ---------------------------------------------------------------------------
# 3b. A control that REFUSES is neither PASS nor FAIL (#1819)
#
# `check()`'s injected-defect loop used to fold a refusal into its list of red
# properties -- `red = ["REFUSED: ..."]` -- so a defect whose evaluation could
# not run was printed `PASS  <defect>: turns red ['REFUSED: ...']` and counted
# toward a green gate. The control "each injected defect must turn a property
# red" then passed having exercised one control fewer: the #1769 shape, a
# REFUSED that is printed but counted as a measurement.
#
# Only defects OUTSIDE `BLIND` are made to refuse here. The blindness loop
# calls `properties` again for its own defects and does not catch `Refused`,
# which #1819 scopes out; a BLIND defect made to refuse escapes `check()` as
# a traceback, and that is a different defect.
# ---------------------------------------------------------------------------

_NOT_BLIND = tuple(d for d in vd.DEFECTS if d not in vd.BLIND)
_REFUSING = _NOT_BLIND[0]
_WHY = f"apparatus off for {_REFUSING}: nothing was measured"


def _properties_that_refuse(refused_defects, *, turn_nothing_red=()):
    """`vd.properties` with the named defects REFUSING, the named others
    measuring an instrument that turns nothing red, and the rest real."""
    real = vd.properties

    def properties(cfg=None, targets=None):
        d = (cfg or {}).get("defect")
        if d in refused_defects:
            raise vd.Refused(f"apparatus off for {d}: nothing was measured")
        if d in turn_nothing_red:
            return real(vd.config(), targets)
        return real(cfg, targets)

    return properties


def test_a_control_that_refuses_is_printed_refused_and_not_counted_as_red(monkeypatch):
    """RED on the pre-#1819 tree, where the line this asserts on read
    `PASS  EQUAL_COLLECTOR_LOADS: turns red ['REFUSED: apparatus off ...']`.

    The line is asserted FIRST, so that red is the behaviour and not the
    two-element tuple `check()` returned there.
    """
    assert _REFUSING not in vd.BLIND and len(_NOT_BLIND) >= 2
    monkeypatch.setattr(vd, "properties", _properties_that_refuse({_REFUSING}))

    res = vd.check()
    line = next(ln for ln in res[1] if _REFUSING in ln)
    assert line == f"  REFUSED  {_REFUSING}: {_WHY}", line

    ok, refused = res[0], res[2]
    # `ok` is the MEASURED verdict. The refusal is not a pass, so it must not
    # be what makes this True, and it is not a failure either.
    assert ok is True
    assert refused == [f"{_REFUSING}: {_WHY}"]


def test_the_clean_run_prints_no_refusal_and_exits_zero(capsys):
    """The paired positive: on the committed read no control refuses, the
    gate says green and the CLI exits PASS."""
    assert vd.main(["--check"]) == exit_contract.PASS == 0
    out = capsys.readouterr().out
    assert "GATE: green" in out
    assert "REFUSED" not in out


def test_main_dash_check_exits_no_evidence_when_a_control_refuses(monkeypatch, capsys):
    """The CLI CI runs (`.github/workflows/rungs.yml`): a refusal is NO
    EVIDENCE, declared in tools/exit_contract.py, never a green gate."""
    monkeypatch.setattr(vd, "properties", _properties_that_refuse({_REFUSING}))

    rc = vd.main(["--check"])

    assert rc == exit_contract.REFUSED_ALTERNATE == 3
    assert rc in exit_contract.NO_EVIDENCE
    out = capsys.readouterr().out
    assert "GATE: REFUSED" in out
    assert "GATE: green" not in out


def test_a_measured_failure_outranks_a_refusal_at_the_exit_status(monkeypatch, capsys):
    """One control refuses while another turns nothing red: the exit is FAIL,
    a result, and the refusal is still reported as one alongside it."""
    other = _NOT_BLIND[1]
    monkeypatch.setattr(vd, "properties", _properties_that_refuse(
        {_REFUSING}, turn_nothing_red={other}))

    assert vd.main(["--check"]) == exit_contract.FAIL == 1
    out = capsys.readouterr().out
    assert f"  REFUSED  {_REFUSING}: {_WHY}" in out
    assert f"  FAIL  {other}: turns red NOTHING" in out
    assert "GATE: RED" in out


def test_every_control_the_defect_loop_can_refuse_refusing_is_no_evidence(monkeypatch, capsys):
    """The edge where every control the defect loop catches a refusal for
    refuses: still NO EVIDENCE (3), not a green gate over six controls fewer
    and not a FAIL. (The BLIND defects in that loop still run for real.)"""
    monkeypatch.setattr(vd, "properties", _properties_that_refuse(set(_NOT_BLIND)))

    assert vd.main(["--check"]) == exit_contract.REFUSED_ALTERNATE
    out = capsys.readouterr().out
    assert out.count("  REFUSED  ") == len(_NOT_BLIND)
    for d in _NOT_BLIND:
        assert f"  PASS  {d}:" not in out and f"  FAIL  {d}:" not in out, d
    assert "GATE: green" not in out


def test_the_record_does_not_call_a_gate_with_a_refused_control_ok(monkeypatch):
    """`record()` writes the gate into `vca-drive.json`. A reader of that
    file who looks only at `gate.ok` must not find `true` beside a control
    that never ran, so the refusal is folded into the recorded verdict and
    named beside it."""
    monkeypatch.setattr(vd, "check", lambda **kw: (True, ["  REFUSED  X: why"], ["X: why"]))
    monkeypatch.setattr(vd, "envelope_record", lambda cfg=None: {})

    rec = vd.record()

    assert rec["gate"] == {"ok": False, "refused": ["X: why"],
                           "lines": ["  REFUSED  X: why"]}


# ---------------------------------------------------------------------------
# 3c. A CLEAN run that REFUSES is NO EVIDENCE, never a traceback (#2056)
#
# #1819's handler covers the injected-defect loop. The call that decides
# whether any control runs at all -- the clean `properties(config(), targets)`
# -- had none, so a refusal there left `check()` as a traceback, through
# `main()` and `record()`, and the shell saw exit 1: the status of a
# comparison that ran and disagreed, for a comparison that never ran. Same
# shape as #1992. The blindness loop's own escape is #1842 and is not touched.
#
# The plant raises the real `vd.Refused` for the clean config ONLY and drives
# the real `check()`, `main()` and `record()`: what is exercised is the
# handler that ships, not a mock of it.
# ---------------------------------------------------------------------------

_CLEAN_WHY = "planted: the clean run cannot be evaluated"
_CLEAN_ROW = f"  REFUSED  clean run: {_CLEAN_WHY}"
_CLEAN_ENTRY = f"clean run: {_CLEAN_WHY}"


def _properties_refusing_the_clean_run():
    """`vd.properties` refusing for the clean config (`defect is None`) and
    measuring every injected defect, blind or not, for real."""
    real = vd.properties

    def properties(cfg=None, targets=None):
        if (cfg or {}).get("defect") is None:
            raise vd.Refused(_CLEAN_WHY)
        return real(cfg, targets)

    return properties


def _properties_failing_one_clean_property():
    """`vd.properties` measuring for real, with ONE property of the clean
    config turned red: a comparison that ran and disagreed."""
    real = vd.properties

    def properties(cfg=None, targets=None):
        got = real(cfg, targets)
        if (cfg or {}).get("defect") is None:
            got = dict(got)
            name = next(iter(got))
            got[name] = {**got[name], "ok": False}
        return got

    return properties


def test_a_refused_clean_run_exits_no_evidence_not_a_traceback(monkeypatch, capsys):
    """RED on the unfixed tree, where this very call raised
    `cymbal_vca_drive.Refused: planted: ...` out of `main()` -- exit 1 at the
    shell, a FAIL for a comparison that never ran."""
    monkeypatch.setattr(vd, "properties", _properties_refusing_the_clean_run())

    rc = vd.main(["--check"])

    assert rc in exit_contract.NO_EVIDENCE
    assert rc == REFUSED == 2 and rc != exit_contract.FAIL
    out = capsys.readouterr().out
    assert _CLEAN_ROW in out
    assert "GATE: REFUSED" in out
    assert "GATE: green" not in out and "GATE: RED" not in out
    # no injected-defect control was evaluated, so none is reported as caught
    assert "-- injected defects" not in out and "-- blindness" not in out
    assert all(f"  {d}" not in out for d in vd.DEFECTS), out


def test_control_the_plant_refuses_the_clean_run_and_nothing_else(monkeypatch):
    """The premise of "no injected-defect control was evaluated": it is the
    gate that stops, not a plant refusing everything. With the plant in place
    every injected defect still measures for real."""
    monkeypatch.setattr(vd, "properties", _properties_refusing_the_clean_run())
    targets = vd.balance_targets()
    for d in vd.DEFECTS:
        assert vd.properties(vd.config(d), targets), d
    with pytest.raises(vd.Refused) as exc:
        vd.properties(vd.config(), targets)
    assert str(exc.value) == _CLEAN_WHY


def test_check_returns_a_refused_clean_run_as_its_own_row(monkeypatch):
    """`check()` names the stage and the reason and claims no verdict: `ok`
    stays True because nothing was measured, so nothing failed, and it is the
    refusal -- not a FAIL -- that keeps the gate from green."""
    monkeypatch.setattr(vd, "properties", _properties_refusing_the_clean_run())

    ok, lines, refused = vd.check()

    assert lines == [_CLEAN_ROW]
    assert refused == [_CLEAN_ENTRY]
    assert ok is True
    assert vd.clean_run_refusal(refused) == _CLEAN_WHY


def test_only_the_clean_runs_own_entry_reads_as_its_refusal():
    """The discriminator the two callers share. A refusal booked against an
    injected defect is not the clean run's, and an empty list is a gate that
    refused nothing."""
    assert vd.clean_run_refusal([]) is None
    assert vd.clean_run_refusal([f"{_REFUSING}: {_WHY}"]) is None
    assert vd.clean_run_refusal(["clean run: why"]) == "why"


def test_a_refused_clean_run_writes_no_record_and_exits_the_same_status(monkeypatch,
                                                                       tmp_path, capsys):
    """`--json` is the other path the traceback took, through `record()`. It
    ends the same way: the declared NO-EVIDENCE status, and no file -- a
    record whose only content is a refusal is not written over whatever
    record was there."""
    monkeypatch.setattr(vd, "properties", _properties_refusing_the_clean_run())
    path = tmp_path / "vca-drive.json"

    assert vd.main(["--json", str(path)]) == REFUSED
    assert vd.main(["--check", "--json", str(path)]) == REFUSED

    assert not path.exists()
    out = capsys.readouterr().out
    assert _CLEAN_WHY in out and "wrote" not in out
    assert "GATE: REFUSED" in out


def test_record_does_not_raise_on_a_refused_clean_run_and_measures_nothing(monkeypatch):
    """`record()` neither raises nor returns a measurement: what it returns
    is the gate alone, saying so."""
    monkeypatch.setattr(vd, "properties", _properties_refusing_the_clean_run())

    rec = vd.record()

    assert set(rec) == {"tool", "gate"}
    assert rec["gate"] == {"ok": False, "refused": [_CLEAN_ENTRY], "lines": [_CLEAN_ROW]}


def test_a_refused_control_still_records_its_measurements(monkeypatch, tmp_path):
    """The control for the two above: a refusal from the injected-defect loop
    is not the clean run's. The clean properties WERE measured, so the record
    still carries them, beside a gate that is not ok, and `--json` writes it."""
    monkeypatch.setattr(vd, "properties", _properties_that_refuse({_REFUSING}))
    path = tmp_path / "vca-drive.json"

    rec = vd.record()
    vd.main(["--json", str(path)])

    assert {"properties", "bandpass", "bandpass_peak", "envelope"} <= set(rec)
    assert rec["gate"]["ok"] is False
    assert rec["gate"]["refused"] == [f"{_REFUSING}: {_WHY}"]
    assert vd.clean_run_refusal(rec["gate"]["refused"]) is None
    assert path.exists()
    written = json.loads(path.read_text())
    assert "properties" in written and written["gate"] == rec["gate"]


def test_a_clean_run_that_measures_a_failure_still_exits_fail(monkeypatch, capsys):
    """The paired positive: a clean property that ran and disagreed is a
    RESULT (1). The REFUSED handler must not absorb it."""
    monkeypatch.setattr(vd, "properties", _properties_failing_one_clean_property())

    assert vd.main(["--check"]) == exit_contract.FAIL == 1
    out = capsys.readouterr().out
    assert "GATE: RED" in out and "REFUSED" not in out


# ---------------------------------------------------------------------------
# 4. Refusals -- preconditions asserted at the point of use
# ---------------------------------------------------------------------------


def test_verify_source_refuses_when_the_scan_is_absent(tmp_path):
    with pytest.raises(vd.SourceUnavailable):
        vd.verify_source(tmp_path / "nope.pdf")


def test_verify_source_refuses_on_a_hash_mismatch(tmp_path):
    p = tmp_path / "sn.pdf"
    p.write_bytes(b"%PDF-1.4 not the service notes")
    with pytest.raises(vd.SourceUnavailable) as exc:
        vd.verify_source(p)
    assert "not the pinned scan" in str(exc.value)


def _verified_source(path) -> dict:
    """What `verify_source` returns for a file that IS the pinned scan."""
    return {"path": path, "sha256": vd.SN_PDF_SHA256, "crops": []}


def test_require_source_is_reachable_from_the_command_line(tmp_path, capsys):
    """The gate step 9 documented and `main()` never read (#369 step 9, W-t-R 7).

    Same tree, same absent scan: PASS without the flag, REFUSED with it
    (#1789 -- it was exit 1, which `tools/run_all.py` reads as a RESULT), and
    the refusal must name the scan rather than failing incidentally.
    """
    assert vd.main(["--check"]) == 0
    capsys.readouterr()
    rc = vd.main(["--check", "--require-source", str(tmp_path / "absent.pdf")])
    out = capsys.readouterr().out
    assert rc == REFUSED
    assert "REFUSED (--require-source)" in out
    assert "GATE:" not in out


def test_a_refused_balance_record_exits_refused_not_fail(tmp_path, capsys, monkeypatch):
    """#1789, the second REFUSED path in `main()`: `balance_targets()`
    refusing. The scan is made to verify -- patched, because the pinned scan
    is a third-party download and deliberately not a test dependency (README
    §1) -- so the `--require-source` guard is behind us and it is the balance
    record's refusal that is measured. Nothing renders: it is raised before
    any mode runs."""
    monkeypatch.setattr(vd, "verify_source", _verified_source)
    monkeypatch.setattr(vd, "BALANCE_ARTIFACT", tmp_path / "no-such-balance.json")
    rc = vd.main(["--check", "--require-source", str(tmp_path / "sn.pdf")])
    out = capsys.readouterr().out
    assert rc == REFUSED
    assert f"REFUSED: {tmp_path / 'no-such-balance.json'} is absent" in out
    assert "REFUSED (--require-source)" not in out
    assert "GATE:" not in out


def test_control_with_both_preconditions_met_the_same_call_passes(tmp_path, capsys,
                                                                 monkeypatch):
    """The injected defect for the two tests above: a guard that always
    returned REFUSED would satisfy them. Same argv as the balance-record
    refusal, both preconditions met -- the scan verifies, the committed
    balance record is where it always was -- and `main()` runs through to
    PASS, reports its gate, and prints no refusal."""
    monkeypatch.setattr(vd, "verify_source", _verified_source)
    rc = vd.main(["--check", "--require-source", str(tmp_path / "sn.pdf")])
    out = capsys.readouterr().out
    assert rc == PASS
    assert "REFUSED" not in out
    assert "GATE: green" in out


def test_verify_source_exits_three_from_the_command_line(tmp_path, capsys):
    rc = vd.main(["--verify-source", str(tmp_path / "absent.pdf")])
    assert rc == 3
    assert "REFUSED" in capsys.readouterr().out


def test_balance_targets_refuses_when_the_record_is_absent(tmp_path):
    with pytest.raises(vd.Refused):
        vd.balance_targets(tmp_path / "balance.json")


def test_balance_targets_refuses_when_the_record_has_drifted(tmp_path):
    rec = json.loads(vd.BALANCE_ARTIFACT.read_text())
    rec["gap_db"]["short"]["gap_db"] = 12.0
    p = tmp_path / "balance.json"
    p.write_text(json.dumps(rec))
    with pytest.raises(vd.Refused) as exc:
        vd.balance_targets(p)
    assert "drifted" in str(exc.value)


def test_balance_targets_accepts_the_committed_record():
    """The refusal above is not vacuous: the real file passes."""
    t = vd.balance_targets()
    assert t["gap_db"]["short"] == pytest.approx(39.79, abs=0.005)
    assert t["gap_db"]["decay"] == pytest.approx(10.13, abs=0.005)


def test_the_pinned_scan_is_the_one_the_vr4_read_used():
    """Two modules reading the same page of the same document must not be able
    to drift onto different printings of it."""
    assert vd.SN_PDF_SHA256 == ts.SN_PDF_SHA256
    assert vd.SN_PDF_PAGE == ts.SN_PDF_PAGE == 13
    assert len(vd.SN_PDF_SHA256) == 64


# ---------------------------------------------------------------------------
# 5. The designator correction, as a computation rather than an assertion
# ---------------------------------------------------------------------------


def _designator_values():
    """designator -> value, from this module's read of the scan."""
    out = {}
    for which, p in vd.BANDPASS.items():
        for d in p["caps"]:
            out[d] = p["cap_f"]
        out[p["shunt"]] = p["shunt_ohm"]
        out[p["fb"]] = p["fb_ohm"]
        out[p["in_cap"]] = p["in_cap_f"]
        out[p["in_res"]] = p["in_res_ohm"]
    return out


@pytest.mark.parametrize("key", ["Hbp1", "Hbp2"])
def test_werner_fig4_source_strings_name_components_that_produce_their_own_f0(key):
    """The mechanism, not a note.

    Two independent things have to agree, and the first draft of this test
    checked only one of them and was therefore VACUOUS -- it passed on the
    exact pre-2026-09-28 string it was written to catch (wrong-then-right 3 in
    `../docs/scorecard/cymbal-369/vca-drive/README.md`):

      1. the DESIGNATORS named must be the ones this read puts on that band;
      2. the CAPACITANCE printed in the string must be the value this read
         gives those designators.

    `Hbp1` declared 3450 Hz with "C13=C14 3.3 nF". Its designators were right;
    its printed value was not (C13/C14 are 6.8 nF, and 3.3 nF on R 560/82 k is
    7117 Hz). Checking only (1) recomputes f0 from the READ's own 6.8 nF and
    sails straight past the wrong number in the text.
    """
    known = wf.KNOWN[key]
    src = known["source"]
    vals = _designator_values()
    caps = [d for d in vals if d.startswith("C") and d in src and vals[d] < 1e-6]
    res = [d for d in vals if d.startswith("R") and d in src]
    assert caps and res, f"{key}: '{src}' names no components this read carries"

    # (1) the designators are one consistent bridged-T in this read
    pair = sorted({vals[d] for d in caps})
    assert len(pair) == 1, f"{key}: '{src}' names caps of differing value {pair}"
    shunt, fb = min(vals[d] for d in res), max(vals[d] for d in res)
    f0 = 1.0 / (2 * math.pi * pair[0] * math.sqrt(shunt * fb))
    assert abs(f0 - known["f0"]) / known["f0"] <= vd.F0_TOL, (
        f"{key}: '{src}' names components that compute {f0:.0f} Hz, "
        f"declares {known['f0']:.0f} Hz")

    # (2) the value PRINTED beside them is the value they have
    m = re.search(r"([0-9.]+)\s*nF", src)
    assert m, f"{key}: '{src}' prints no capacitance to check"
    assert float(m.group(1)) * 1e-9 == pytest.approx(pair[0], rel=0.02), (
        f"{key}: '{src}' prints {m.group(1)} nF where {caps} are "
        f"{pair[0] * 1e9:.1f} nF")


@pytest.mark.parametrize("pin,f0,caps,res", [
    ("IC3 pin 1", "3.45 kHz", ("C13", "C14"), ("R56", "R57")),
    ("IC3 pin 7", "7.1 kHz", ("C15", "C16"), ("R58", "R59")),
])
def test_reference_section_10_names_the_designators_this_read_found(pin, f0, caps, res):
    """The reference is the document the model is built from, so the pairing is
    gated where a reader would look it up, not only in this module."""
    text = (ROOT / "docs" / "tr808-reference.md").read_text()
    # "560" pins this to the row that actually carries component values: the
    # "high, variable" row also says "IC3 pin 7" and "7.1 kHz" but names no
    # parts, and picking it by position would make this test order-dependent.
    rows = [ln for ln in text.splitlines()
            if ln.startswith("|") and pin in ln and f0 in ln and "560" in ln
            and "bridged-T type" not in ln]
    assert len(rows) == 1, f"§10 rows for {pin} / {f0}: {len(rows)}"
    row = rows[0]
    for d in caps + res:
        assert d in row, f"§10's {pin} row does not name {d}: {row}"


# ---------------------------------------------------------------------------
# 6. What this artifact deliberately does NOT do
# ---------------------------------------------------------------------------


def test_this_artifact_is_not_at_the_path_that_would_flip_396s_refusal():
    """`cymbal_band_balance.preconditions()` gates on a file's EXISTENCE and
    never reads its contents, so dropping any file at `vca-drive.json` would
    shorten #396's refusal list without a single drive number reaching the
    balance. This module therefore writes to `vca-drive/vca-drive.json`
    instead, and the mismatch is asserted rather than left to be noticed.
    Filed as a follow-up so the gate is fixed where it lives.
    """
    import cymbal_band_balance as bal
    assert vd.ARTIFACT != bal.VCA_ARTIFACT
    assert not bal.VCA_ARTIFACT.exists(), (
        "a bare vca-drive.json now exists; #396's precondition would pass "
        "without consuming a single number from it")


def test_the_record_round_trips(tmp_path):
    p = tmp_path / "vca-drive.json"
    assert vd.main(["--json", str(p)]) == 0
    rec = json.loads(p.read_text())
    assert rec["gate"]["ok"] is True
    assert rec["vca_term_db"]["chain_db"]["short"] == pytest.approx(4.973, abs=0.005)
    assert rec["source"]["sha256"] == vd.SN_PDF_SHA256


# ---------------------------------------------------------------------------
# 7. #432 -- the three envelope generators' peak collector voltages
#
# The bounds every test below is judged against are module constants, and each
# one is asserted to be what its stated derivation gives BEFORE it is used, so
# a bound cannot be quietly widened to admit a number:
#   ENV_SPAN_FACTOR      the three external sources' own disagreement, 1.36,
#                        rounded up -- not a tolerance chosen after the fact
#   ENV_SPAN_EXTERNAL    Roland SN p.14 and two Fischer measurements, verbatim
# ---------------------------------------------------------------------------


def test_the_span_bound_is_the_external_sources_own_disagreement():
    """The bound is derived, not chosen. If this test is ever "fixed" by
    raising ENV_SPAN_FACTOR, the known answer below stops being one."""
    ext = vd.ENV_SPAN_EXTERNAL
    assert ext["chart"] == pytest.approx(1200.0 / 350.0, abs=1e-9)
    assert ext["low"] == pytest.approx(1280.0 / 400.0, abs=1e-9)
    assert ext["decay"] == pytest.approx(1090.0 / 250.0, abs=1e-9)
    disagreement = max(ext.values()) / min(ext.values())
    assert disagreement == pytest.approx(1.3625, abs=0.001)
    assert vd.ENV_SPAN_FACTOR == 1.6
    assert vd.ENV_SPAN_FACTOR >= disagreement
    assert vd.ENV_SPAN_FACTOR < 2.0, "a factor of 2 would admit almost anything"


@pytest.mark.parametrize("key,band", [("low", "low"), ("decay", "decay"),
                                      ("chart", "composite")])
def test_the_decay_knob_span_reproduces_the_machine(key, band):
    """THE EXTERNAL KNOWN ANSWER FOR #432.

    How far the CY decay moves when DECAY is swept end to end is stated by
    three artifacts none of which knew about this schematic read: Roland's own
    chart (SN p.14, 350 -> 1200 ms) and two measurements off the Fischer 808
    recordings (`docs/scorecard/cymbal-369/README.md` S1, made by
    `tools/cymbal_bands.py` in an earlier step). This is a RATIO, so C41's
    absolute value, the undefined meaning of the chart's "decay time" and an
    EDT's amplitude calibration all cancel -- which is exactly why it is the
    quantity worth gating on.
    """
    want = vd.ENV_SPAN_EXTERNAL[key]
    got = vd.envelope_spans()[band]["span"]
    assert want / vd.ENV_SPAN_FACTOR <= got <= want * vd.ENV_SPAN_FACTOR, (
        f"{band}: predicted span {got:.3f}, external {want:.3f}")


@pytest.mark.parametrize("duty", vd.ENV_DUTY)
@pytest.mark.parametrize("beta", [100.0, 200.0, 400.0])
def test_the_span_known_answer_survives_both_unknowns(duty, beta):
    """...and it is not a coincidence at one operating point.

    The two quantities the schematic does NOT print are the swing VCAs'
    conduction duty and the transistors' beta. Every combination of the
    bracketed values has to stay inside the same bound, otherwise the known
    answer is really a statement about a chosen duty.
    """
    spans = vd.envelope_spans(vd.config(), duty, beta)
    for key, band in (("low", "low"), ("decay", "decay"), ("chart", "composite")):
        want = vd.ENV_SPAN_EXTERNAL[key]
        got = spans[band]["span"]
        assert want / vd.ENV_SPAN_FACTOR <= got <= want * vd.ENV_SPAN_FACTOR, (
            f"duty={duty} beta={beta} {band}: {got:.3f} vs {want:.3f}")


def test_the_reference_says_parallel_and_the_scan_says_series():
    """The correction, as a computation rather than an assertion.

    S10 records "VR2 2 MOhm || R93 470 kOhm". Read that way, the DECAY knob's
    minimum is a SHORT to ground -- C41's tail becomes zero and the low band
    stops existing. The span known answer above rejects it; this test states
    the mechanism as well as the verdict.
    """
    bad = vd.config("VR2_PARALLEL_NOT_SERIES")
    want = vd.ENV_SPAN_EXTERNAL["low"]
    ok_span = vd.envelope_spans()["low"]["span"]
    assert want / vd.ENV_SPAN_FACTOR <= ok_span <= want * vd.ENV_SPAN_FACTOR

    # The mechanism: read in parallel, DECAY at minimum puts a short across
    # C41, so the low band's reservoir never charges at all.
    dead = vd.envelope_peaks(bad, 1.0, 0.0)["low"]["reservoir_peak_v"]
    alive = vd.envelope_peaks(vd.config(), 1.0, 0.0)["low"]["reservoir_peak_v"]
    assert alive > 12.0 and dead < 3.0, (alive, dead)

    # ...and the verdict: the span property the recordings gate goes red.
    props = vd.envelope_properties(bad)
    assert not props["env-decay-knob-span-low"]["ok"], props["env-decay-knob-span-low"]

    # the series pair is 470 k .. 2.47 M and never zero
    lo = vd.env_resistors(vd.config(), 0.0)
    hi = vd.env_resistors(vd.config(), 1.0)
    assert sum(r[2] for r in lo if r[3] in ("R93", "VR2")) == pytest.approx(470e3, rel=1e-3)
    assert sum(r[2] for r in hi if r[3] in ("R93", "VR2")) == pytest.approx(2.47e6, rel=1e-9)


def test_the_reference_section_10_row_states_the_series_pair():
    """Gated where a reader would look it up, not only in this module."""
    text = (ROOT / "docs" / "tr808-reference.md").read_text()
    rows = [ln for ln in text.splitlines()
            if ln.startswith("|") and "VR2" in ln and "C41" in ln and "R93" in ln]
    assert rows, "S10 has no row naming VR2 / R93 / C41"
    for row in rows:
        # A correction that records what it replaced is worth more than one
        # that silently overwrites, so the superseded wording is allowed to
        # survive INSIDE a `was "..."` quotation and nowhere else.
        live = re.sub(r'was\s+"[^"]*"', "was <superseded>", row)
        assert "series" in live.lower(), live
        assert "‖" not in live and "||" not in live, live
        assert "0.38" not in live, live


def test_the_three_reservoirs_are_equal_at_the_peak():
    """HALF the hypothesis #432 was filed with, and the half that holds.

    C38, C40 and C41 are all 1 uF and all charged from Q19's emitter through
    their own diode, and the 1 ms trigger is long against the charging path.
    The bound is not a tolerance: it is n*Vt*ln(I_hi/I_lo) at the load currents
    the transient reports, which differ by two orders of magnitude.
    """
    sp = vd.envelope_peak_spread()
    assert sp["ok"], sp
    assert sp["spread_v"] < 0.05, sp
    assert sp["bound_v"] > 0.15, "the bound must be the real diode spread, not zero"
    lo, hi = min(sp["load_current_a"].values()), max(sp["load_current_a"].values())
    assert hi / lo > 50.0, "the bound is only interesting because the currents differ"


def test_a_mismatched_diode_breaks_the_reservoir_equality():
    """The paired negative. Equality nobody has seen fail is not a measurement."""
    bad = vd.envelope_peak_spread(vd.config("MISMATCHED_D8"))
    assert not bad["ok"], bad
    assert bad["spread_v"] > bad["bound_v"]


def test_the_collector_peaks_are_not_equal_at_any_duty():
    """THE OTHER HALF, and it is refuted.

    Only the short band's collector load hangs on its own reservoir. The DECAY
    band's is behind R88/C39 and a divider, the low band's behind Q20 and
    R105/C45 -- 10 to 70 ms of lag against a 1 ms trigger.
    """
    for duty in vd.ENV_DUTY:
        pk = vd.envelope_peaks(vd.config(), duty)
        assert pk["short"]["collector_peak_db_re_low"] >= vd.ENV_PEAK_UNEQUAL_DB
        assert pk["decay"]["collector_peak_db_re_low"] <= vd.ENV_DECAY_BELOW_LOW_DB
        # ...and the short band's ceiling IS its reservoir, which is what makes
        # it the exception rather than merely the largest.
        assert pk["short"]["collector_peak_v"] == pytest.approx(
            pk["short"]["reservoir_peak_v"], rel=1e-6)


@pytest.mark.parametrize("band,frac", [("decay", 0.45), ("low", 0.70)])
def test_the_two_smoothed_bands_never_reach_their_reservoirs(band, frac):
    """The mechanism behind the refutation, stated as a number per band."""
    pk = vd.envelope_peaks()
    ratio = pk[band]["collector_peak_v"] / pk[band]["reservoir_peak_v"]
    assert ratio < frac, f"{band}: ceiling is {ratio:.3f} of its reservoir"


def test_the_accent_range_scales_the_peaks_and_barely_moves_the_ratios():
    """S1.1's 4-14 V trigger moves every peak by 4.6x and every inter-band
    ratio by at most 1.1 dB.

    The residual is not noise and is worth naming: the two V_BE / V_f offsets
    Q19, Q20 and the diodes subtract are a fixed 0.6 V, which is 21 % of a
    2.8 V reservoir and 4.7 % of a 12.8 V one. So the per-band asymmetry is
    very nearly a property of the printed network rather than of how hard the
    voice is hit -- but only very nearly, and the short band's advantage is
    1.0 dB SMALLER at accent minimum.
    """
    lo = vd.envelope_peaks(vd.config(), 1.0, 1.0, vd.ENV_TRIG_V[0])
    hi = vd.envelope_peaks(vd.config(), 1.0, 1.0, vd.ENV_TRIG_V[1])
    assert hi["short"]["reservoir_peak_v"] / lo["short"]["reservoir_peak_v"] > 4.0
    for band in vd.ENV_BAND:
        assert lo[band]["collector_peak_db_re_low"] == pytest.approx(
            hi[band]["collector_peak_db_re_low"], abs=1.2)
    # the direction of that residual, stated rather than absorbed by the bound
    assert (lo["short"]["collector_peak_db_re_low"]
            < hi["short"]["collector_peak_db_re_low"])


def test_the_transient_reproduces_the_closed_form_short_band_modes():
    """The two formulations, on the sub-network that stands alone.

    C38 touches only R87 and R94, so the SHORT band's envelope is a three-node
    linear network whose modes are an eigenvalue problem a reader can check.
    The MNA transient's deep tail must reproduce the slowest of them.
    """
    modes = vd.env_short_band_modes()
    slowest = max(modes)
    tr = vd.envelope_transient(vd.config(), 1.0, 1.0)
    measured = vd.env_tau_ms(tr["t"], tr["clip"]["short"], -30.0, -50.0)
    assert slowest == pytest.approx(161.8, rel=0.02), modes
    assert measured == pytest.approx(slowest, rel=0.03), (measured, slowest)


def test_control_the_two_formulations_would_notice_a_changed_resistor():
    """...and that agreement is not two names for one code path."""
    cfg = vd.config()
    bad = json.loads(json.dumps(cfg["env"]))
    for row in bad["res"]:
        if row[3] == "R94":
            row[2] = 390e3
    perturbed = dict(cfg, env=bad)
    assert max(vd.env_short_band_modes(perturbed)) > 2.0 * max(vd.env_short_band_modes(cfg))


def test_the_short_band_is_blind_to_the_decay_knob_and_that_is_verified():
    """The recordings say the LOW band tracks DECAY; S10 says only the middle
    band does. The schematic says both of those bands move and the short one
    does not, which is the ordering the recordings show."""
    spans = vd.envelope_spans()
    assert spans["short"]["span"] == pytest.approx(1.0, abs=vd.ENV_BLIND_SPAN_TOL)
    assert spans["low"]["span"] > 2.0
    assert spans["decay"]["span"] > 2.0
    # and the low band moves at least as much as the DECAY band's own reservoir
    # network would on its own -- VR2 reaches the low band directly and the
    # DECAY band only through R92/R89/R91.
    assert spans["low"]["tau_max_ms"] > spans["decay"]["tau_max_ms"]


def test_the_ceilings_do_not_even_peak_at_the_same_time():
    pk = vd.envelope_peaks()
    assert pk["short"]["collector_peak_ms"] == pytest.approx(vd.ENV_TRIG_MS, abs=0.2)
    assert pk["low"]["collector_peak_ms"] > vd.ENV_PEAK_TIME_RATIO * vd.ENV_TRIG_MS
    assert pk["decay"]["collector_peak_ms"] > 5.0


def test_the_clip_ceiling_still_cannot_close_396s_gap():
    """Step 11's negative, restated for the large-signal case it could not reach.

    The ceiling is an upper bound on what a band can deliver, so the LARGEST
    admissible value is the one to argue against -- here the most favourable
    duty in the bracket.
    """
    best = max(vd.envelope_peaks(vd.config(), d)["short"]["collector_peak_db_re_low"]
               for d in vd.ENV_DUTY)
    gap = vd.balance_targets()["gap_db"]["short"]
    assert best < 12.0, best
    assert gap - best >= vd.GAP_MARGIN_DB, (gap, best)


@pytest.mark.parametrize("defect", ["VR2_PARALLEL_NOT_SERIES", "WRONG_C41",
                                    "FAST_SMOOTHING", "NO_SMOOTHING_CAPS",
                                    "MISMATCHED_D8", "R89_OPEN"])
def test_every_envelope_defect_turns_an_envelope_property_red(defect):
    """A stricter statement than the generic control: an envelope defect must
    turn an ENVELOPE property red, not merely something somewhere."""
    got = vd.envelope_properties(vd.config(defect))
    red = [n for n, p in got.items() if not p["ok"]]
    assert red, f"{defect} turned no envelope property red"


def test_the_envelope_netlist_is_the_one_on_the_scan():
    """Every component in the envelope section, pinned by designator and value.

    This is the read itself; if a value here is wrong every number above is
    wrong in the same way, so it is written down where it can be diffed.
    """
    want = {"R87": 22e3, "R94": 39e3, "R88": 33e3, "R90": 33e3, "R91": 33e3,
            "R89": 10e3, "R92": 33e3, "R93": 470e3, "R105": 33e3, "R104": 22e3}
    got = {r[3]: r[2] for r in vd.ENV_RES}
    assert got == want
    caps = {d: v for v, d in vd.ENV_CAP.values()}
    assert caps["C38"] == pytest.approx(1e-6)
    assert caps["C37"] == pytest.approx(2.2e-6)
    assert caps["C40"] == pytest.approx(1e-6)
    assert caps["C39"] == pytest.approx(0.47e-6)
    assert caps["C41"] == pytest.approx(1e-6)
    assert caps["C45"] == pytest.approx(2.2e-6)
    assert vd.ENV_POT["ohm"] == pytest.approx(2e6)
    assert vd.ENV_POT["taper"] == "B"
    assert [d for _n, d in vd.ENV_DIODE] == ["D6", "D7", "D8"]
    # the supply node is the reservoir for the short band only
    assert vd.ENV_BAND["short"]["supply"] == vd.ENV_BAND["short"]["reservoir"] == "A"
    assert vd.ENV_BAND["decay"]["supply"] != vd.ENV_BAND["decay"]["reservoir"]
    assert vd.ENV_BAND["low"]["supply"] != vd.ENV_BAND["low"]["reservoir"]


def test_the_envelope_crops_are_on_the_same_page_as_the_rest_of_the_read():
    for name in ("env-q19", "env-q20"):
        assert name in vd.SN_CROPS
        assert vd.SN_CROPS[name]["dpi"] >= 600
    assert vd.SN_PDF_PAGE == 13


def test_the_supply_record_round_trips(tmp_path):
    p = tmp_path / "vca-supply.json"
    assert vd.main(["--json-supply", str(p)]) == 0
    rec = json.loads(p.read_text())
    assert rec["gate"]["ok"] is True
    assert rec["source"]["sha256"] == vd.SN_PDF_SHA256
    assert set(rec["source"]["crops"]) == {"env-q19", "env-q20"}
    assert rec["reference_correction"]["document"].startswith("docs/tr808-reference.md")
    peaks = rec["envelope"]["peaks_by_duty_and_vr2"]["1.0"]["1.0"]
    assert peaks["short"]["collector_peak_v"] == pytest.approx(12.79, abs=0.05)
    assert peaks["low"]["collector_peak_v"] == pytest.approx(4.94, abs=0.10)
    assert peaks["decay"]["collector_peak_v"] == pytest.approx(3.97, abs=0.10)
