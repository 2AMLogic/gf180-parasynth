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


def test_an_injected_control_cannot_write_into_the_board():
    assert score.main(["--case", "F1A", "--inject", "F1_CHAIN_DROP_DECIM"]) == 2


def test_the_injection_names_match_the_rtl_paths_own_list():
    """A control the scorer offers but the path cannot compile is an unsatisfiable
    gate; a defect the path knows and the scorer hides is an unused one."""
    assert set(rtlpath.INJECTS_EXERCISED) <= set(rtlpath.INJECTS)
    assert set(rtlpath.INJECTS_NOT_EXERCISED) <= set(rtlpath.INJECTS)
    assert set(rtlpath.INJECTS_EXERCISED) | set(rtlpath.INJECTS_NOT_EXERCISED) \
        == set(rtlpath.INJECTS)
