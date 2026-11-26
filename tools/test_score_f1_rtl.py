"""The F1 RTL scorer's own logic: the twin comparison, and the guards that stop
an engine substitution from being recorded under the wrong label.

The simulation itself is not exercised here (two curves of iverilog is about
twenty minutes). What is exercised is everything that could silently mislabel or
silently overwrite: the engine guard in `run_case.run_filter_case`, the
disagreement report, and the refusal to let a control land on the board.
"""
import json

import pytest

import f1_rtl_filter_path as rtlpath
import run_case as rc
import score_f1_rtl as score


class _StubPath:
    """Enough of `SelectedFilterPath`'s interface to reach the engine guard."""
    render_label = "a stub"
    path_version = "stub-v0"

    def __init__(self):
        self.probe_profile = {"name": "selected"}
        self.calibration = "surge-type2-clean-v1"
        self.match = {"frames": 12000}

    def curve(self, freqs, cut_hz, res, amp):      # pragma: no cover - guard fires first
        raise AssertionError("the guard should have refused before any curve was measured")

    def record(self):                              # pragma: no cover - guard fires first
        raise AssertionError("not reached")


def _case():
    return next(c for c in rc.load_cases() if c["case_id"] == "F1A")


def test_a_substituted_path_must_name_its_engine():
    """Refused before any reference is loaded or any RTL runs: a twenty-minute
    simulation labelled `fixed-model` would be worse than no measurement."""
    with pytest.raises(rc.Refused, match="must name its engine"):
        rc.run_filter_case(_case(), "", False, engine_path=_StubPath())
    with pytest.raises(rc.Refused, match="must name its engine"):
        rc.run_filter_case(_case(), "", False, engine_path=_StubPath(),
                           engine=rc.ENGINE)


def test_a_substituted_path_cannot_be_combined_with_a_reference_injection():
    with pytest.raises(rc.Refused, match="cannot be combined"):
        rc.run_filter_case(_case(), "REF_CORNER_2X", False, engine_path=_StubPath(),
                           engine="integrated-rtl")


def _metric(value, reference, error, units="Hz", tol=11.76):
    return {"value": value, "units": units, "reference": reference, "error": error,
            "tolerance": tol, "valid": True, "tolerance_basis": "frequency"}


def _twin():
    return {"engine": "fixed-model", "case_id": "F1A",
            "source_commit": "abc1234", "analysis_run": "run_case@dead",
            "metrics": {"Corner frequency": _metric(108.6651, 117.6344, -8.9693)}}


def _rtl(value=108.6651, error=-8.9693):
    out = json.loads(json.dumps(_twin()))
    out["engine"] = "integrated-rtl"
    out["metrics"]["Corner frequency"] = _metric(value, 117.6344, error)
    return out


def test_identical_readings_are_reported_as_agreement():
    case = _case()
    cmp = score.compare_with_twin(case, _twin(), _rtl())
    assert cmp["twin_present"] and cmp["agrees"]
    assert cmp["metrics"]["Corner frequency"]["same"] is True
    assert cmp["metrics_that_moved"] == []
    assert cmp["twin_engine"] == "fixed-model"


def test_a_moved_metric_is_named_rather_than_averaged_away():
    case = _case()
    cmp = score.compare_with_twin(case, _twin(), _rtl(value=131.0, error=13.4))
    assert cmp["agrees"] is False
    assert cmp["metrics_that_moved"] == ["Corner frequency"]
    moved = cmp["metrics"]["Corner frequency"]
    assert moved["twin_value"] == 108.6651 and moved["rtl_value"] == 131.0
    assert moved["delta"] == pytest.approx(22.3349, abs=1e-6)
    # the board state is compared too, not only the numbers
    assert cmp["twin_state"] != cmp["rtl_state"] or cmp["state_changed"] is False


def test_a_change_below_the_float_noise_floor_is_not_called_a_disagreement():
    case = _case()
    cmp = score.compare_with_twin(case, _twin(), _rtl(value=108.66515, error=-8.96925))
    assert cmp["metrics"]["Corner frequency"]["same"] is True


def test_a_missing_twin_is_stated_not_assumed_to_agree():
    cmp = score.compare_with_twin(_case(), None, _rtl())
    assert cmp["twin_present"] is False and "agrees" not in cmp


def test_a_missing_twin_names_why_it_is_missing():
    cmp = score.compare_with_twin(
        _case(), None, _rtl(),
        why_missing="the board slot holds an integrated-rtl anchor and no fixed-model "
                    "result is parked beside it")
    assert "integrated-rtl anchor" in cmp["why"]


def test_the_comparison_records_where_the_twin_was_read_from():
    cmp = score.compare_with_twin(_case(), _twin(), _rtl(),
                                  twin_where="the board slot results/F1A.json")
    assert "board slot" in cmp["twin_read_from"]


def test_stream_agreement_sums_every_curve_and_flags_a_mismatch():
    record = {"model_path": {"rtl_chain": {"curves": [
        {"tag": "a", "cut_hz": 250.0,
         "rtl_vs_model": {"frames": 100, "mismatches": 0, "bit_exact": True}},
        {"tag": "b", "cut_hz": 20000.0,
         "rtl_vs_model": {"frames": 100, "mismatches": 3, "bit_exact": False}}]}}}
    agree = score.stream_agreement(record)
    assert agree["total_frames"] == 200 and agree["total_mismatches"] == 3
    assert agree["bit_exact"] is False
    assert score.stream_agreement({})["bit_exact"] is False


def _board(tmp_path):
    board = tmp_path / "results"
    board.mkdir()
    return board


def _write(path, record):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record))


def test_a_first_run_reads_the_twin_from_the_board_slot(tmp_path):
    """The layout the first anchor saw: the board slot holds the fixed-model
    reading this record is about to replace."""
    board = _board(tmp_path)
    _write(board / "F1A.json", _twin())
    twin, where, why, unreadable = score.find_model_twin("F1A", board)
    assert twin is not None and twin["engine"] == "fixed-model"
    assert "board slot" in where and "F1A.json" in where
    assert why is None and unreadable is None


def test_a_repeat_run_reads_the_model_twin_parked_beside_the_anchor(tmp_path):
    """The repeat-run layout: the board slot holds the previous RTL anchor, and
    `run_case.result_destination` parks a fixed-model result beside it under
    `results/model-twin/` (run_case.py). That, not the anchor, is the twin."""
    board = _board(tmp_path)
    _write(board / "F1A.json", _rtl())
    _write(board / rc.MODEL_TWIN_DIR / "F1A.json", _twin())
    twin, where, why, unreadable = score.find_model_twin("F1A", board)
    assert twin is not None and twin["engine"] == "fixed-model"
    assert rc.MODEL_TWIN_DIR in where
    assert why is None and unreadable is None


def test_an_rtl_board_row_with_no_model_twin_is_a_missing_comparison(tmp_path):
    """The layout that produced the published defect: an anchor in the slot and
    no fixed-model result anywhere. The comparison is missing, not assumed
    against whatever occupies the slot (#1390)."""
    board = _board(tmp_path)
    _write(board / "F1A.json", _rtl())
    twin, where, why, unreadable = score.find_model_twin("F1A", board)
    assert twin is None and where is None and unreadable is None
    assert "integrated-rtl" in why and rc.MODEL_TWIN_DIR in why


def test_a_board_row_that_names_another_case_is_not_the_twin(tmp_path):
    board = _board(tmp_path)
    other = _twin()
    other["case_id"] = "F1B"
    _write(board / "F1A.json", other)
    twin, where, why, unreadable = score.find_model_twin("F1A", board)
    assert twin is None and unreadable is None
    assert "F1B" in why


def test_an_unreadable_candidate_refuses_rather_than_guesses(tmp_path):
    board = _board(tmp_path)
    _write(board / "F1A.json", _rtl())
    unreadable_path = board / rc.MODEL_TWIN_DIR / "F1A.json"
    unreadable_path.parent.mkdir(parents=True, exist_ok=True)
    unreadable_path.write_text("{not json")
    twin, where, why, unreadable = score.find_model_twin("F1A", board)
    assert twin is None and unreadable is not None and "unreadable" in unreadable


def test_a_candidate_that_is_not_a_json_object_refuses(tmp_path):
    """Valid JSON whose top level is not a record is the same failure: present,
    but unusable as a twin. The old lookup crashed on it."""
    board = _board(tmp_path)
    _write(board / "F1A.json", _rtl())
    _write(board / rc.MODEL_TWIN_DIR / "F1A.json", "a json string, not a record")
    twin, where, why, unreadable = score.find_model_twin("F1A", board)
    assert twin is None and unreadable is not None and "not a JSON object" in unreadable


def test_no_board_record_at_all_is_a_missing_comparison(tmp_path):
    board = _board(tmp_path)
    twin, where, why, unreadable = score.find_model_twin("F1A", board)
    assert twin is None and unreadable is None
    assert "no earlier record" in why


def test_an_injected_control_cannot_write_into_the_board():
    assert score.main(["--case", "F1A", "--inject", "F1_CHAIN_DROP_DECIM"]) == 2


def test_the_injection_names_match_the_rtl_paths_own_list():
    """A control the scorer offers but the path cannot compile is an unsatisfiable
    gate; a defect the path knows and the scorer hides is an unused one."""
    assert set(rtlpath.INJECTS_EXERCISED) <= set(rtlpath.INJECTS)
    assert set(rtlpath.INJECTS_NOT_EXERCISED) <= set(rtlpath.INJECTS)
    assert set(rtlpath.INJECTS_EXERCISED) | set(rtlpath.INJECTS_NOT_EXERCISED) \
        == set(rtlpath.INJECTS)
