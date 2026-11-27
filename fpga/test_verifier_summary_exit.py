"""The controls.json summary and exit status of the three multi-run Arty verifiers.

fpga/verify_arty_controls.py, fpga/verify_pads_top.py and
fpga/verify_uart_bridge.py each run several children and summarise them. A
child that REFUSED, or that never ran (no simulator, a compile error, a
timeout), has no verdict. Summarising that as FAIL and exiting 1 renders an
unknown in the place where a result belongs -- the defect tools/exit_contract.py
exists to prevent, and the status tools/run_all.py reads as NO-VERDICT.

The precedence, stated once and pinned here for each runner:

    any child without a verdict   ->  REFUSED, exit 2
    else any child FAIL           ->  FAIL,    exit 1
    else                          ->  PASS,    exit 0

so a real FAIL never masks a sibling that did not run, and a refused child is
never rendered as a caught or a missed defect. Testing only all-refused suites
would miss a runner that refuses only when every child refused; every runner
is therefore also driven with one PASS beside one refusal, and with one FAIL
beside one refusal.

No simulator is needed: each child is replaced at the boundary its runner
calls (the child process for verify_arty_controls, simulate() and analyze()
for the other two), so the real run_one / do / record_for / summary code runs.
The expected exit statuses are written as the integers, not imported from the
contract, so these tests check the runners against the documented 0 / 1 / 2
rather than against the constant the runners themselves read.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

import verify_arty_controls as vac
import verify_pads_top as vp
import verify_uart_bridge as vub

EXIT = {"PASS": 0, "FAIL": 1, "REFUSED": 2, "NO VERDICT": 2}


def _controls(outdir: Path) -> dict:
    return json.loads((outdir / "controls.json").read_text())


def _assert_records_agree(outdir: Path, runs: dict, outcomes: dict) -> None:
    """Each child that ran left a verification.json saying what the summary
    says it says, with that state's exit status; a child that did not run left
    none. The two runners label a missing record differently ("NO VERDICT" in
    pads, "REFUSED" in uart), so the record's presence is read from the child's
    outcome, never inferred from that label."""
    for name, state in runs.items():
        path = outdir / name / "verification.json"
        if outcomes[name] == "none":
            assert not path.exists(), f"{name}: did not run, yet left a record"
            continue
        rec = json.loads(path.read_text())
        assert (rec["state"], rec["exit_code"]) == (state, EXIT[state]), name


# ---- fpga/verify_arty_controls.py -----------------------------------------

CLEAN = {"state": "PASS", "exit_code": 0}
MOSI_CAUGHT = {"state": "FAIL", "exit_code": 1, "comparison": {"writes_bad": 3}}
SDATA_CAUGHT = {"state": "FAIL", "exit_code": 1,
                "comparison": {"periods": 900, "wire_mismatch": 900, "core_bad": 0}}
CHILD_REFUSED = {"state": "REFUSED", "exit_code": 2}


class _Child:
    """Stands where verify_arty.py's process stands: leaves the record a real
    child writes, then exits with its status. `record=None` is a child that
    died before writing one (an import crash); `hang` never returns."""

    pid = 424242

    def __init__(self, outdir, record, rc, hang=False):
        self.outdir, self.record, self.rc, self.hang = outdir, record, rc, hang

    def wait(self, timeout=None):
        if self.hang:
            raise subprocess.TimeoutExpired("verify_arty.py", timeout)
        if self.record is not None:
            (Path(self.outdir) / "verification.json").write_text(json.dumps(self.record))
        return self.rc


def _arty(monkeypatch, tmp_path, outcomes) -> int:
    """Run verify_arty_controls against stand-in children. `outcomes` maps each
    mutation (None is the clean run) to (record, exit status), "crash" or "hang"."""
    def popen(command, **_kwargs):
        mutation = command[command.index("--inject") + 1] if "--inject" in command else None
        outcome = outcomes[mutation]
        outdir = command[command.index("--outdir") + 1]
        if outcome in ("crash", "hang"):
            return _Child(outdir, None, None, hang=outcome == "hang")
        return _Child(outdir, *outcome)

    monkeypatch.setattr(vac.subprocess, "Popen", popen)
    if "hang" in outcomes.values():
        monkeypatch.setattr(vac.os, "killpg", lambda *_args: None)
    monkeypatch.setattr(sys, "argv", ["verify_arty_controls.py", "--outdir", str(tmp_path)])
    return vac.main()


ARTY_CASES = [
    ("clean_passes_and_both_controls_are_caught",
     {None: (CLEAN, 0), "ARTY_MOSI_ZERO": (MOSI_CAUGHT, 1),
      "ARTY_SDATA_ZERO": (SDATA_CAUGHT, 1)},
     {"state": "PASS", "clean_passed": True,
      "controls_caught": {"ARTY_MOSI_ZERO": True, "ARTY_SDATA_ZERO": True}}, 0),
    ("every_child_refused",
     {None: (CHILD_REFUSED, 2), "ARTY_MOSI_ZERO": (CHILD_REFUSED, 2),
      "ARTY_SDATA_ZERO": (CHILD_REFUSED, 2)},
     {"state": "REFUSED", "refused": ["clean", "ARTY_MOSI_ZERO", "ARTY_SDATA_ZERO"],
      "clean_passed": False,
      "controls_caught": {"ARTY_MOSI_ZERO": False, "ARTY_SDATA_ZERO": False}}, 2),
    ("clean_passes_beside_a_refused_control",
     {None: (CLEAN, 0), "ARTY_MOSI_ZERO": (MOSI_CAUGHT, 1),
      "ARTY_SDATA_ZERO": (CHILD_REFUSED, 2)},
     {"state": "REFUSED", "refused": ["ARTY_SDATA_ZERO"], "clean_passed": True,
      "controls_caught": {"ARTY_MOSI_ZERO": True, "ARTY_SDATA_ZERO": False}}, 2),
    ("a_missed_control_beside_a_refused_sibling",
     {None: (CLEAN, 0), "ARTY_MOSI_ZERO": (CLEAN, 0),
      "ARTY_SDATA_ZERO": (CHILD_REFUSED, 2)},
     {"state": "REFUSED", "refused": ["ARTY_SDATA_ZERO"], "clean_passed": True,
      "controls_caught": {"ARTY_MOSI_ZERO": False, "ARTY_SDATA_ZERO": False}}, 2),
    ("a_failing_clean_beside_two_caught_controls",
     {None: (MOSI_CAUGHT, 1), "ARTY_MOSI_ZERO": (MOSI_CAUGHT, 1),
      "ARTY_SDATA_ZERO": (SDATA_CAUGHT, 1)},
     {"state": "FAIL", "clean_passed": False,
      "controls_caught": {"ARTY_MOSI_ZERO": False, "ARTY_SDATA_ZERO": False}}, 1),
    ("every_child_ran_and_one_control_was_missed",
     {None: (CLEAN, 0), "ARTY_MOSI_ZERO": (MOSI_CAUGHT, 1),
      "ARTY_SDATA_ZERO": (CLEAN, 0)},
     {"state": "FAIL", "clean_passed": True,
      "controls_caught": {"ARTY_MOSI_ZERO": True, "ARTY_SDATA_ZERO": False}}, 1),
]


@pytest.mark.parametrize("outcomes, summary, code",
                         [(o, s, c) for _id, o, s, c in ARTY_CASES],
                         ids=[case[0] for case in ARTY_CASES])
def test_arty_summary_state_and_exit(monkeypatch, tmp_path, outcomes, summary, code):
    assert _arty(monkeypatch, tmp_path, outcomes) == code
    assert _controls(tmp_path) == summary


def test_arty_child_that_died_before_writing_its_record_refuses_the_suite(monkeypatch, tmp_path):
    """A child that stops at an import writes no verification.json and exits 1.
    That says nothing about the wrapper: it is no verdict, not a FAIL."""
    outcomes = {None: (CLEAN, 0), "ARTY_MOSI_ZERO": (MOSI_CAUGHT, 1),
                "ARTY_SDATA_ZERO": "crash"}
    assert _arty(monkeypatch, tmp_path, outcomes) == 2
    assert _controls(tmp_path)["refused"] == ["ARTY_SDATA_ZERO"]
    assert not (tmp_path / "ARTY_SDATA_ZERO" / "verification.json").exists()


def test_arty_child_that_times_out_refuses_the_suite(monkeypatch, tmp_path):
    outcomes = {None: "hang", "ARTY_MOSI_ZERO": (MOSI_CAUGHT, 1),
                "ARTY_SDATA_ZERO": (SDATA_CAUGHT, 1)}
    assert _arty(monkeypatch, tmp_path, outcomes) == 2
    assert _controls(tmp_path) == {
        "state": "REFUSED", "refused": ["clean"], "clean_passed": False,
        "controls_caught": {"ARTY_MOSI_ZERO": False, "ARTY_SDATA_ZERO": False}}


def test_arty_refused_retry_invalidates_prior_pass_record(monkeypatch, tmp_path):
    """A PASS followed by a child that dies before writing its record in the
    same outdir must not leave the prior PASS verification.json on disk."""
    passed = {None: (CLEAN, 0), "ARTY_MOSI_ZERO": (MOSI_CAUGHT, 1),
              "ARTY_SDATA_ZERO": (SDATA_CAUGHT, 1)}
    assert _arty(monkeypatch, tmp_path, passed) == 0
    assert json.loads((tmp_path / "clean" / "verification.json").read_text())["state"] == "PASS"
    crashed = {None: "crash", "ARTY_MOSI_ZERO": (MOSI_CAUGHT, 1),
               "ARTY_SDATA_ZERO": (SDATA_CAUGHT, 1)}
    assert _arty(monkeypatch, tmp_path, crashed) == 2
    assert _controls(tmp_path)["state"] == "REFUSED"
    assert not (tmp_path / "clean" / "verification.json").exists()


# ---- fpga/verify_pads_top.py ----------------------------------------------

def _pads(monkeypatch, outdir: Path, outcomes) -> int:
    """Run the pads suite with each scenario standing for one child outcome:
    "pass", "fail", "precondition" (analyze refuses) or "none" (no simulator)."""
    def simulate(scenario, inject, path, **_kwargs):
        if outcomes[scenario] == "none":
            return None
        Path(path).mkdir(parents=True, exist_ok=True)
        return {"outdir": Path(path), "srcs": [], "inject": inject, "scenario": scenario}

    def analyze(run):
        if outcomes[run["scenario"]] == "precondition":
            return False, {"precondition": ["the model's loudest sample is 0 LSB"]}, []
        return outcomes[run["scenario"]] == "pass", {}, []

    monkeypatch.setattr(vp, "simulate", simulate)
    monkeypatch.setattr(vp, "analyze", analyze)
    return vp.main(["--outdir", str(outdir), "--scenario", "all", "--jobs", "1"])


PADS_ALL = ("phases", "switch", "reset")


def _pads_runs(**overrides) -> dict:
    return {sc: overrides.get(sc, "pass") for sc in PADS_ALL}


PADS_CASES = [
    ("every_scenario_passes", _pads_runs(),
     {"phases": "PASS", "switch": "PASS", "reset": "PASS"}, "PASS", 0),
    ("no_simulator_so_no_scenario_ran", _pads_runs(phases="none", switch="none", reset="none"),
     {"phases": "NO VERDICT", "switch": "NO VERDICT", "reset": "NO VERDICT"}, "REFUSED", 2),
    ("one_pass_beside_a_precondition_refusal", _pads_runs(switch="precondition"),
     {"phases": "PASS", "switch": "REFUSED", "reset": "PASS"}, "REFUSED", 2),
    ("one_pass_beside_a_scenario_that_did_not_run", _pads_runs(phases="none"),
     {"phases": "NO VERDICT", "switch": "PASS", "reset": "PASS"}, "REFUSED", 2),
    ("a_real_fail_beside_a_refused_sibling", _pads_runs(phases="fail", switch="precondition"),
     {"phases": "FAIL", "switch": "REFUSED", "reset": "PASS"}, "REFUSED", 2),
    ("every_scenario_ran_and_one_failed", _pads_runs(switch="fail"),
     {"phases": "PASS", "switch": "FAIL", "reset": "PASS"}, "FAIL", 1),
]


@pytest.mark.parametrize("outcomes, runs, state, code",
                         [(o, r, s, c) for _id, o, r, s, c in PADS_CASES],
                         ids=[case[0] for case in PADS_CASES])
def test_pads_summary_state_exit_and_records(monkeypatch, tmp_path, outcomes, runs, state, code):
    assert _pads(monkeypatch, tmp_path, outcomes) == code
    assert _controls(tmp_path) == {"state": state, "runs": runs}
    _assert_records_agree(tmp_path, runs, outcomes)


@pytest.mark.parametrize("state, ok, expected", [
    (None, True, ("PASS", 0)),
    (None, False, ("FAIL", 1)),
    ("REFUSED", False, ("REFUSED", 2)),
    ("NO VERDICT", False, ("NO VERDICT", 2)),
])
def test_pads_record_exit_code_follows_its_resolved_state(tmp_path, state, ok, expected):
    """A precondition refusal or NO VERDICT resolves the record to that state;
    its exit_code must be 2, as the kit-hash refusal record beside it already does."""
    run = {"srcs": [], "inject": None, "scenario": "phases", "outdir": tmp_path}
    rec = vp.record_for(run, {}, ok, state)
    assert (rec["state"], rec["exit_code"]) == expected


def test_pads_precondition_refusal_leaves_a_record_that_exits_two(monkeypatch, tmp_path):
    """Through the real run_one: analyze() refusing is written to disk as a
    REFUSED record whose exit_code is 2, before the summary ever reads it."""
    _pads(monkeypatch, tmp_path, _pads_runs(phases="precondition"))
    rec = json.loads((tmp_path / "phases" / "verification.json").read_text())
    assert (rec["state"], rec["exit_code"]) == ("REFUSED", 2)


def test_pads_refused_retry_invalidates_prior_pass_records(monkeypatch, tmp_path):
    """A PASS followed by an unavailable simulator in the same outdir must not
    leave the prior PASS verification.json files on disk."""
    assert _pads(monkeypatch, tmp_path, _pads_runs()) == 0
    for sc in PADS_ALL:
        rec = json.loads((tmp_path / sc / "verification.json").read_text())
        assert (rec["state"], rec["exit_code"]) == ("PASS", 0)
    none_outcomes = _pads_runs(phases="none", switch="none", reset="none")
    assert _pads(monkeypatch, tmp_path, none_outcomes) == 2
    assert _controls(tmp_path) == {
        "state": "REFUSED",
        "runs": {sc: "NO VERDICT" for sc in PADS_ALL},
    }
    _assert_records_agree(tmp_path, {sc: "NO VERDICT" for sc in PADS_ALL}, none_outcomes)


# ---- fpga/verify_uart_bridge.py -------------------------------------------


def _uart(monkeypatch, outdir: Path, outcomes) -> int:
    """Run the UART suite with each scenario standing for one child outcome:
    "pass", "fail", "empty" (zero comparisons) or "none" (no simulation)."""
    def simulate(scenario, inject, path, **_kwargs):
        if outcomes[scenario] == "none":
            return None
        Path(path).mkdir(parents=True, exist_ok=True)
        (Path(path) / "transcript.txt").write_text("")
        return {"outdir": path, "inject": inject, "scenario": scenario}

    def analyze_verdict(run):
        state = {"empty": "NO VERDICT", "pass": "PASS", "fail": "FAIL"}[outcomes[run["scenario"]]]
        return state, {}, []

    monkeypatch.setattr(vub, "simulate", simulate)
    monkeypatch.setattr(vub, "analyze_verdict", analyze_verdict)
    return vub.main(["--outdir", str(outdir), "--scenario", "all", "--jobs", "1"])


def _uart_runs(**overrides) -> dict:
    return {sc: overrides.get(sc, "pass") for sc in vub.SCENARIOS}


def _uart_states(**overrides) -> dict:
    """What controls.json's `runs` says when every scenario passed except the
    overrides, which name the state a run is summarised as."""
    return {sc: overrides.get(sc, "PASS") for sc in vub.SCENARIOS}


UART_CASES = [
    ("every_scenario_passes", _uart_runs(), _uart_states(), "PASS", 0),
    ("no_simulator_so_no_scenario_ran", _uart_runs(**{sc: "none" for sc in vub.SCENARIOS}),
     _uart_states(**{sc: "REFUSED" for sc in vub.SCENARIOS}), "REFUSED", 2),
    ("one_scenario_that_did_not_run_beside_passes", _uart_runs(overflow="none"),
     _uart_states(overflow="REFUSED"), "REFUSED", 2),
    ("a_real_fail_beside_a_sibling_that_did_not_run", _uart_runs(overflow="none", phrase="fail"),
     _uart_states(overflow="REFUSED", phrase="FAIL"), "REFUSED", 2),
    ("a_real_fail_beside_a_sibling_with_zero_comparisons", _uart_runs(phrase="empty", overflow="fail"),
     _uart_states(phrase="NO VERDICT", overflow="FAIL"), "REFUSED", 2),
    ("every_scenario_ran_and_one_failed", _uart_runs(phrase="fail"),
     _uart_states(phrase="FAIL"), "FAIL", 1),
]


@pytest.mark.parametrize("outcomes, runs, state, code",
                         [(o, r, s, c) for _id, o, r, s, c in UART_CASES],
                         ids=[case[0] for case in UART_CASES])
def test_uart_summary_state_exit_and_records(monkeypatch, tmp_path, outcomes, runs, state, code):
    assert _uart(monkeypatch, tmp_path, outcomes) == code
    assert _controls(tmp_path) == {"state": state, "runs": runs}
    _assert_records_agree(tmp_path, runs, outcomes)


def test_uart_record_ships_the_whole_comparison(tmp_path):
    """The record's comparison is analyze()'s whole dict, not a filtered subset:
    build_arty's gate reads keys (periods, writes_sent, worst_strobe_cycle)
    that a narrowed copy would have to anticipate one key at a time."""
    (tmp_path / "transcript.txt").write_text("")
    comp = {"periods": 900, "writes_sent": 5, "periods_required": 900}
    rec = vub.record_for({"outdir": tmp_path, "inject": None, "scenario": "held"}, comp, "PASS")
    assert rec["comparison"] is comp
    assert (rec["state"], rec["exit_code"]) == ("PASS", 0)


def test_uart_refused_retry_invalidates_prior_pass_records(monkeypatch, tmp_path):
    """A PASS followed by an unavailable simulator in the same outdir must not
    leave the prior PASS verification.json files on disk."""
    assert _uart(monkeypatch, tmp_path, _uart_runs()) == 0
    for sc in vub.SCENARIOS:
        rec = json.loads((tmp_path / sc / "verification.json").read_text())
        assert (rec["state"], rec["exit_code"]) == ("PASS", 0)
    none_outcomes = _uart_runs(**{sc: "none" for sc in vub.SCENARIOS})
    none_states = _uart_states(**{sc: "REFUSED" for sc in vub.SCENARIOS})
    assert _uart(monkeypatch, tmp_path, none_outcomes) == 2
    assert _controls(tmp_path) == {"state": "REFUSED", "runs": none_states}
    _assert_records_agree(tmp_path, none_states, none_outcomes)


def test_uart_single_run_refused_retry_invalidates_prior_records(monkeypatch, tmp_path):
    """--start-red and --replay also return early when simulation is unavailable
    and must clear any prior verification.json in that run directory."""
    start_red_rec = tmp_path / "start-red" / "verification.json"
    start_red_rec.parent.mkdir(parents=True, exist_ok=True)
    start_red_rec.write_text(json.dumps({"state": "FAIL", "exit_code": 1}))
    monkeypatch.setattr(vub, "simulate", lambda *_a, **_kw: None)
    assert vub.main(["--outdir", str(tmp_path), "--start-red"]) == 2
    assert not start_red_rec.exists()

    replay_rec = tmp_path / "phrase" / "verification.json"
    replay_rec.parent.mkdir(parents=True, exist_ok=True)
    replay_rec.write_text(json.dumps({"state": "PASS", "exit_code": 0}))
    monkeypatch.setattr(vub, "simulate_replay", lambda *_a, **_kw: None)
    assert vub.main(["--outdir", str(tmp_path), "--replay", str(tmp_path / "cap"),
                     "--replay-name", "phrase"]) == 2
    assert not replay_rec.exists()


def test_uart_replay_no_verdict_sets_exit_code_two(monkeypatch, tmp_path):
    """When --replay marks verification.json NO VERDICT and exits 2, the
    recorded exit_code must also be 2."""
    def fake_replay(_prefix, path):
        Path(path).mkdir(parents=True, exist_ok=True)
        (Path(path) / "transcript.txt").write_text("")
        return {"outdir": Path(path), "inject": None, "scenario": "replay"}

    monkeypatch.setattr(vub, "simulate_replay", fake_replay)
    monkeypatch.setattr(vub, "analyze_verdict", lambda _run: (
        "NO VERDICT", {"periods": 10, "periods_required": 900, "i2s_incomplete": True}, []))
    monkeypatch.setattr(vub, "truncation_control", lambda _run: {"caught": True})
    assert vub.main(["--outdir", str(tmp_path), "--replay", str(tmp_path / "cap"),
                     "--replay-name", "phrase"]) == 2
    rec = json.loads((tmp_path / "phrase" / "verification.json").read_text())
    assert (rec["state"], rec["exit_code"]) == ("NO VERDICT", 2)
