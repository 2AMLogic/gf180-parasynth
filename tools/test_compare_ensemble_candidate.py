"""The engine gate, and the other ways an ensemble comparison can lie.

The first test is the reason this file exists: an Ensemble case has one result
path, so an `integrated-rtl` anchor overwrites the `fixed-model` twin, and a
comparison that accepted a `fixed-model` candidate would certify the model
against itself and report it as instrument evidence.
"""
import copy

import pytest

import compare_ensemble_candidate as compare
import run_case as rc
from score_ensemble_i2s import Refused


def _metrics(*, timing=2.0, balance=0.0, rail=0.0):
    return {
        "Event timing": {"value": timing, "units": "ms", "reference": 0.0, "error": timing,
                         "tolerance": 10.0, "valid": True, "tolerance_basis": "event timing",
                         "worst_stop": "BD"},
        "bus balance": {"value": balance, "units": "dB", "reference": 0.0, "error": balance,
                        "tolerance": 0.5, "valid": True, "tolerance_basis": "bus sum"},
        "output artifacts": {"value": rail, "units": "% of samples", "reference": 0.0,
                            "error": rail, "tolerance": 0.01, "valid": True,
                            "tolerance_basis": "rail"},
    }


def _record(*, engine="integrated-rtl", case_id="E1A", control=True, **metric_kw):
    diagnostics = {}
    if engine == "integrated-rtl":
        diagnostics = {
            "decoded_i2s_sha256": {"mix": "abc", "stop:BD": "def"},
            "per_stop_event_timing": {"BD": {"scheduled": 10, "valid": True,
                                             "offset_ms": 2.0}},
        }
        diagnostics["negative_control"] = (
            {"inject": "DRUM_BUS_STALE", "caught": True} if control
            else {"inject": "DRUM_BUS_STALE", "caught": False})
    return {"engine": engine, "case_id": case_id, "subject": "Bass and kit / sparse",
            "source_commit": "deadbeef",
            "reference_profile": "our own per-bus stems and the register schedule",
            "render_run": "synth_top.v over the SPI pins",
            "tolerance_policy": copy.deepcopy(rc.TOLERANCE_POLICY),
            "metrics": _metrics(**metric_kw),
            "diagnostics": diagnostics,
            "provenance": {"engine": engine, "worktree": {"commit": "deadbeef"},
                           "command": "tools/score_ensemble_i2s.py --case E1A",
                           "inputs": {"docs/scorecard/cases.csv": "sha256:1"}}}


# --------------------------------------------------------------------------- #
# the gate
# --------------------------------------------------------------------------- #
def test_rejects_a_candidate_that_is_not_integrated_rtl():
    baseline = _record(engine="integrated-rtl")
    candidate = _record(engine="fixed-model")
    with pytest.raises(Refused, match="candidate must name engine 'integrated-rtl' "
                                      r"\(saw 'fixed-model'\)"):
        compare.compare_records(baseline, candidate)


@pytest.mark.parametrize("engine", ["float-model", "board-digital", "", None, "rtl"])
def test_rejects_any_candidate_engine_other_than_integrated_rtl(engine):
    candidate = _record(engine=engine)
    # an engine the board knows but that is not the instrument, and engines it
    # does not know at all, are both refused -- and for the same reason
    with pytest.raises(Refused, match="integrated-rtl"):
        compare.compare_records(_record(), candidate)


def test_rejects_two_fixed_model_records():
    with pytest.raises(Refused, match="integrated-rtl"):
        compare.compare_records(_record(engine="fixed-model"),
                                _record(engine="fixed-model"))


def test_rejects_a_candidate_with_no_caught_negative_control():
    with pytest.raises(Refused, match="no CAUGHT negative control"):
        compare.compare_records(_record(), _record(control=False))


def test_rejects_an_integrated_record_that_binds_no_decoded_i2s():
    candidate = _record()
    candidate["diagnostics"].pop("decoded_i2s_sha256")
    with pytest.raises(Refused, match="binds no decoded I2S evidence"):
        compare.compare_records(_record(), candidate)


def test_rejects_an_integrated_record_with_no_per_stop_timing():
    candidate = _record()
    candidate["diagnostics"]["per_stop_event_timing"] = {}
    with pytest.raises(Refused, match="no per-stop event timing"):
        compare.compare_records(_record(), candidate)


# --------------------------------------------------------------------------- #
# the measurement contract
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("mutation,match", [
    # the board names the missing required property in its own words (#1601)
    (lambda rec: rec["metrics"].pop("bus balance"),
     ("no pass/fail verdict on the board: " "missing required: bus balance")),
    (lambda rec: rec["metrics"]["Event timing"].update(tolerance=30.0), "measurement contract"),
    (lambda rec: rec["metrics"]["bus balance"].update(units="dBFS"), "measurement contract"),
    (lambda rec: rec["metrics"]["output artifacts"].update(valid=False),
     "is invalid, so it has no distance"),
    (lambda rec: rec["metrics"]["Event timing"].update(error=float("nan")),
     "no pass/fail verdict on the board: invalid: Event timing \\(invalid number: error is nan\\)"),
    (lambda rec: rec["tolerance_policy"].update({"bus sum": "0.5 dB, whatever"}),
     "not the frozen contract"),
    (lambda rec: rec["provenance"].pop("inputs"), "no provenance inputs"),
])
def test_refuses_an_incomparable_candidate(mutation, match):
    candidate = _record()
    mutation(candidate)
    with pytest.raises(Refused, match=match):
        compare.compare_records(_record(), candidate)


def test_refuses_records_for_different_cases():
    with pytest.raises(Refused, match="different cases"):
        compare.compare_records(_record(case_id="E1B"), _record(case_id="E1A"))


def test_refuses_a_case_that_is_not_an_ensemble_case():
    with pytest.raises(Refused, match="M5A belongs to the Mono family, but this comparison "
                                      "reads Ensemble cases"):
        compare.compare_records(_record(case_id="M5A"), _record(case_id="M5A"))


# --------------------------------------------------------------------------- #
# what it reports when it does compare
# --------------------------------------------------------------------------- #
def test_cross_engine_comparison_names_its_mode_and_agreement():
    baseline = _record(engine="fixed-model", timing=1.9583)
    candidate = _record(timing=2.0417)
    report = compare.compare_records(baseline, candidate)
    assert report["comparison_mode"] == "cross-engine"
    assert report["comparison"]["states_agree"] is True
    assert report["comparison"]["case_passes"] is True
    assert report["comparison"]["accepts_candidate"] is True
    timing = report["comparison"]["metrics"]["Event timing"]
    assert timing["baseline_error"] == 1.9583 and timing["candidate_error"] == 2.0417
    assert report["comparison"]["regressed_components"] == ["Event timing"]
    # a regression well inside the tolerance is reported, never hidden
    assert timing["candidate_inside_tolerance"] is True
    assert abs(timing["delta_fraction_of_tolerance"] - 0.00834) < 1e-6


def test_rtl_to_rtl_comparison_is_named_separately():
    report = compare.compare_records(_record(timing=3.0), _record(timing=2.0))
    assert report["comparison_mode"] == "rtl-to-rtl"
    assert report["comparison"]["improved_components"] == ["Event timing"]


def test_a_candidate_that_loses_a_pass_is_not_accepted_however_much_else_improved():
    baseline = _record(engine="fixed-model", timing=5.0, balance=0.4)
    candidate = _record(timing=1.0, balance=0.9)          # timing better, bus sum broken
    report = compare.compare_records(baseline, candidate)
    assert report["comparison"]["lost_passes"] == ["bus balance"]
    assert report["comparison"]["case_passes"] is False
    assert report["comparison"]["accepts_candidate"] is False
    assert report["comparison"]["states_agree"] is False


def test_disagreement_between_the_engines_is_visible_in_the_report():
    baseline = _record(engine="fixed-model")             # passes
    candidate = _record(rail=0.5)                        # rails: fails
    report = compare.compare_records(baseline, candidate)
    assert report["baseline"]["state"] == "pass"
    assert report["candidate"]["state"] == "fail"
    assert report["comparison"]["states_agree"] is False


# --------------------------------------------------------------------------- #
# THE BOARD's verdict, recomputed -- never the record's own (#1601)
# --------------------------------------------------------------------------- #
# `evaluate` was called but its state was then ignored beside whatever the
# record said about itself, and nothing asked the provenance which engine it
# had measured.

def test_refuses_a_candidate_whose_header_says_chip_and_whose_provenance_says_model():
    candidate = _record()
    candidate["provenance"]["engine"] = "fixed-model"
    with pytest.raises(Refused, match="candidate provenance and record disagree about the engine"):
        compare.compare_records(_record(), candidate)


@pytest.mark.parametrize("side", ["baseline", "candidate"])
@pytest.mark.parametrize("state", ["fail", None])
def test_refuses_a_record_whose_stored_state_contradicts_the_board(side, state):
    """`_record()` passes on the board. A stored `fail` contradicts it, and a
    stored null is an unknown in the place where a result belongs."""
    baseline, candidate = _record(), _record()
    (baseline if side == "baseline" else candidate)["scorecard_state"] = state
    with pytest.raises(Refused, match=f"{side} stores state {state!r} but the board "
                                      r"evaluates it as 'pass'"):
        compare.compare_records(baseline, candidate)


def test_a_stored_state_that_agrees_with_the_board_is_accepted():
    """The control for the test above: carrying the key is not the refusal."""
    candidate = _record()
    candidate["scorecard_state"] = "pass"
    assert compare.compare_records(_record(), candidate)["comparison"]["case_passes"] is True


@pytest.mark.parametrize("mutation,match", [
    # accepted: nothing asked the provenance which engine it had measured
    (lambda rec: rec["provenance"].pop("engine"), "provenance and record disagree"),
    # reached float(): OverflowError
    (lambda rec: rec["metrics"]["bus balance"].update(error=10**400),
     "no pass/fail verdict on the board: int too large"),
    # compared, and written with a bare `Infinity`: the error overflows the 0.01 tolerance
    (lambda rec: rec["metrics"]["output artifacts"].update(error=1.7976931348623157e308),
     "no finite distance on the board"),
])
def test_refuses_inputs_the_hand_rolled_gate_accepted_or_crashed_on(mutation, match):
    """Each row was compared as data, or ended the process with a traceback
    (exit 1), on the hand-rolled gate; REFUSED is exit 2."""
    candidate = _record()
    mutation(candidate)
    with pytest.raises(Refused, match=match):
        compare.compare_records(_record(), candidate)


@pytest.mark.parametrize("mutation,match", [
    (lambda rec: rec.update(provenance=["x"]), "provenance is absent, null or not an object"),
    (lambda rec: rec.update(metrics=[]), "carries no metrics"),
    (lambda rec: rec["metrics"].update({"bus balance": 0.0}), "bus balance is malformed"),
    (lambda rec: rec["metrics"]["bus balance"].update(valid="yes"), "bus balance is invalid"),
    (lambda rec: rec["metrics"]["bus balance"].update(valid=1), "bus balance is invalid"),
    (lambda rec: rec["metrics"]["bus balance"].update(value="0.0"), "invalid number: value"),
])
def test_still_refuses_what_the_deleted_local_checks_refused(mutation, match):
    """Regression pins for the deletion: every input the local `valid`,
    numeric and shape checks refused must stay refused without them."""
    candidate = _record()
    mutation(candidate)
    with pytest.raises(Refused, match=match):
        compare.compare_records(_record(), candidate)


@pytest.mark.parametrize("side", ["baseline", "candidate"])
def test_a_non_object_json_document_is_refused_not_a_crash(side):
    """A valid JSON file that is not an object (`[]`) reached `.get` and died
    with AttributeError: exit 1 and a traceback, where REFUSED is exit 2."""
    baseline, candidate = [], _record()
    if side == "candidate":
        baseline, candidate = _record(), []
    with pytest.raises(Refused, match=f"{side} is not a record"):
        compare.compare_records(baseline, candidate)


def test_refuses_a_changed_scoring_direction_on_bus_balance():
    """Bus balance -1 dB: a match is 2 tolerances out; a ceiling clamps to 0."""
    baseline, candidate = _record(balance=-1.0), _record(balance=-1.0)
    candidate["metrics"]["bus balance"]["purpose"] = "defect ceiling"
    with pytest.raises(Refused, match="bus balance is scored as 'defect ceiling'"):
        compare.compare_records(baseline, candidate)
