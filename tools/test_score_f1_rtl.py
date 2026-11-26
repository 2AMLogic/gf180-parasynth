"""The F1 RTL scorer's own logic: the twin comparison, and the guards that stop
an engine substitution from being recorded under the wrong label.

The simulation itself is not exercised here (two curves of iverilog is about
twenty minutes). What is exercised is everything that could silently mislabel or
silently overwrite: the engine guard in `run_case.run_filter_case`, the
disagreement report, the refusal to let a control land on the board, and the
refusal classification (#1357) -- a missing frozen clip or a substituted
engine's own refusal is exit 2, never exit 1's measured-mismatch status, and
an unrelated exception is never relabelled as missing evidence.
"""
import json

import numpy as np
import pytest

import f1_rtl_filter_path as rtlpath
import refprofile as rp
import refusal
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


# ===========================================================================
# #1357: a missing frozen clip is a REFUSAL (exit 2), not a measured mismatch
# (exit 1). The loader raises `refprofile.Refused`, which the old engine-only
# catch tuple and the name-classifying handler below it both mishandled.
# ===========================================================================
class _NoSimulatorPath:
    """The engine side of the missing-clip control: no simulator is involved
    (the loader refuses first), and a curve that runs is itself a failure --
    the refusal must precede the measurement."""
    tools = {"iverilog": "stubbed (no simulator in this control)"}

    def __init__(self, inject=None, timeout_s=None):
        assert inject is None, "this control injects nothing"

    def curve(self, freqs, cut_hz, res, amp):
        raise AssertionError("the missing clip must refuse before any curve "
                             "is measured")

    def record(self):
        raise AssertionError("not reached")


def test_missing_frozen_audio_is_exit_2_not_a_measured_mismatch(
        tmp_path, monkeypatch, capsys):
    """The refusal a clean checkout produces: the committed profile names the
    clip, the gitignored cache is absent, and the real loader (only its
    location is redirected) raises `refprofile.Refused`. The scorer must turn
    that into exit 2 naming the clip, with no result JSON -- the status a
    consumer reads as "no measurement", distinct from exit 1's "measured and
    it disagrees". The batch premise gate is stubbed because this control is
    the loader's refusal, not the batch's (that gate has its own tests)."""
    real_profile = score.ROOT / "refprofile" / "profile.json"
    if not real_profile.exists():
        pytest.skip("no committed profile in this tree")
    clip = rc.FILTER_CASES["F1A"]["ref_clip"]
    prof = json.loads(real_profile.read_text())
    pdir = tmp_path / "refprofile"
    pdir.mkdir()
    (pdir / "profile.json").write_text(json.dumps(
        {"schema": prof["schema"], "clips": {clip: prof["clips"][clip]}}))
    monkeypatch.setattr(rp, "PROFILE_JSON", pdir / "profile.json")
    monkeypatch.setattr(rp, "PROFILE_DIR", pdir)
    monkeypatch.setattr(rtlpath, "RtlFilterChainPath", _NoSimulatorPath)
    monkeypatch.setattr(rc, "base_check", lambda allow_stale=False: {})
    outdir = tmp_path / "f1-results"
    assert score.main(["--case", "F1A", "--no-audio", "--results", str(outdir)]) == 2
    err = capsys.readouterr().err
    assert "REFUSED" in err and clip in err
    assert not (outdir / "F1A.json").exists()


def _reference_good_enough_to_reach_the_first_curve(monkeypatch):
    """Stub reference data: this control's subject is the exception a
    substituted engine raises from its first curve, not the reference side
    (the real loader is the control above). One shared probe grid, so the
    pre-curve grid assertions pass and the first curve is reached."""
    freqs = np.asarray([100.0, 200.0, 400.0])
    meta = {"freqs_hz": [100.0, 200.0, 400.0]}
    gains = np.asarray([-1.0, -2.0, -4.0])
    monkeypatch.setattr(rc, "load_filter_reference",
                        lambda clip_id, inject="": (freqs, gains, meta))


def _engine_raising(exc_factory, monkeypatch):
    class _RaisingPath:
        tools = {"iverilog": "stubbed (no simulator in this control)"}

        def __init__(self, inject=None, timeout_s=None):
            pass

        def curve(self, freqs, cut_hz, res, amp):
            raise exc_factory()

        def record(self):
            raise AssertionError("not reached")

    monkeypatch.setattr(rtlpath, "RtlFilterChainPath", _RaisingPath)


def test_a_differently_named_refusal_subclass_from_the_engine_is_exit_2(
        tmp_path, monkeypatch, capsys):
    """A substituted engine's own refusal may be named anything: what makes it
    a refusal is the shared base, not the name. The name-classifying handler
    let this escape as a traceback. The class is defined inside the test on
    purpose: `tools/check_refused_base.py` scans top-level definitions, and
    the unrelated namesake in the next test must stay invisible to it."""
    class EngineRefusal(refusal.Refused):
        pass

    _reference_good_enough_to_reach_the_first_curve(monkeypatch)
    _engine_raising(lambda: EngineRefusal("the RTL chain could not be probed"),
                    monkeypatch)
    monkeypatch.setattr(rc, "base_check", lambda allow_stale=False: {})
    outdir = tmp_path / "f1-results"
    assert score.main(["--case", "F1A", "--no-audio", "--results", str(outdir)]) == 2
    err = capsys.readouterr().err
    assert "REFUSED" in err and rtlpath.ENGINE in err
    assert "could not be probed" in err
    assert not (outdir / "F1A.json").exists()


def test_an_unrelated_exception_named_refused_is_an_execution_error(
        tmp_path, monkeypatch, capsys):
    """The inverse defect, and the reason the name check was worse than
    nothing: a `RuntimeError` subclass named `Refused` is an implementation
    error wearing a refusal's name, and the name classifier relabelled it as
    missing evidence -- exit 2 -- instead of letting it fail loudly. Nested
    on purpose: it is non-conforming by design, so the conformance guard must
    not see it."""
    class Refused(RuntimeError):
        pass

    _reference_good_enough_to_reach_the_first_curve(monkeypatch)
    _engine_raising(lambda: Refused("boom: not a refusal"), monkeypatch)
    monkeypatch.setattr(rc, "base_check", lambda allow_stale=False: {})
    with pytest.raises(RuntimeError, match="not a refusal") as excinfo:
        score.main(["--case", "F1A", "--no-audio", "--results",
                    str(tmp_path / "f1-results")])
    assert type(excinfo.value) is Refused


def test_a_plain_runtime_error_from_the_engine_still_propagates(
        tmp_path, monkeypatch):
    """Neither a refusal nor wearing one's name: an ordinary engine bug stays
    an execution error. Unchanged behaviour, pinned so the shared-base handler
    cannot quietly widen into `except Exception`."""
    _reference_good_enough_to_reach_the_first_curve(monkeypatch)
    _engine_raising(lambda: RuntimeError("boom"), monkeypatch)
    monkeypatch.setattr(rc, "base_check", lambda allow_stale=False: {})
    with pytest.raises(RuntimeError, match="boom"):
        score.main(["--case", "F1A", "--no-audio", "--results",
                    str(tmp_path / "f1-results")])


def test_the_injection_names_match_the_rtl_paths_own_list():
    """A control the scorer offers but the path cannot compile is an unsatisfiable
    gate; a defect the path knows and the scorer hides is an unused one."""
    assert set(rtlpath.INJECTS_EXERCISED) <= set(rtlpath.INJECTS)
    assert set(rtlpath.INJECTS_NOT_EXERCISED) <= set(rtlpath.INJECTS)
    assert set(rtlpath.INJECTS_EXERCISED) | set(rtlpath.INJECTS_NOT_EXERCISED) \
        == set(rtlpath.INJECTS)
