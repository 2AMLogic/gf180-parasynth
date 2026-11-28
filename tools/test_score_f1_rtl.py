"""The F1 RTL scorer's own logic: the twin comparison, and the guards that stop
an engine substitution from being recorded under the wrong label.

The simulation itself is not exercised here (two curves of iverilog is about
twenty minutes). What is exercised is everything that could silently mislabel or
silently overwrite: the engine guard in `run_case.run_filter_case`, the
disagreement report, the refusal to let a control land on the board, and the
CLI handler's exit contract -- a refusal from any module the run reaches is
REFUSED (2), never a traceback (#1643).
"""
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import exit_contract
import f1_rtl_filter_path as rtlpath
import refusal
import refprofile as rp
import run_case as rc
import score_f1_rtl as score


class _StubPath:
    """Enough of `SelectedFilterPath`'s interface to reach the engine guard."""
    render_label = "a stub"
    path_version = "stub-v0"
    engine = None            # a substitute that never declared an identity

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


@pytest.mark.parametrize("attributes", [
    {}, {"engine": None}, {"engine": ""}, {"engine": "   "},
    {"engine": ["integrated-rtl"]}, {"engine": rc.ENGINE}, {"engine": " fixed-model "},
], ids=["missing", "none", "empty", "blank", "non-string", "reserved", "padded-reserved"])
def test_a_substituted_path_must_name_its_engine(attributes):
    """Refused before any reference is loaded or any RTL runs: a twenty-minute
    simulation labelled `fixed-model` would be worse than no measurement."""
    with patch.object(rc, "load_filter_reference",
                      side_effect=AssertionError("reached reference I/O")):
        with pytest.raises(rc.Refused, match="must name its engine"):
            rc.run_filter_case(_case(), "", False, engine_path=SimpleNamespace(**attributes))


def test_a_substituted_path_cannot_be_combined_with_a_reference_injection():
    class _Named(_StubPath):
        engine = "integrated-rtl"
    with pytest.raises(rc.Refused, match="cannot be combined"):
        rc.run_filter_case(_case(), "REF_CORNER_2X", False, engine_path=_Named())


def test_the_engine_label_is_not_independently_selectable():
    """#1492 control: the old call shape `engine="integrated-rtl"` with no path
    must not be accepted, and must never reach reference I/O."""
    with patch.object(rc, "load_filter_reference",
                      side_effect=AssertionError("reached reference I/O")):
        with pytest.raises(TypeError):
            rc.run_filter_case(_case(), "", False, engine="integrated-rtl")


def test_each_path_declares_the_identity_its_records_carry():
    import f1_filter_path as fp
    assert fp.SelectedFilterPath.engine == rc.ENGINE == "fixed-model"
    assert rtlpath.RtlFilterChainPath.engine == rtlpath.ENGINE == "integrated-rtl"


@pytest.mark.parametrize("use_default, engine", [(True, "default-probe"), (False, "integrated-rtl")])
def test_record_curve_file_and_provenance_carry_the_path_engine(use_default, engine, tmp_path):
    """Both supported paths through the real scorer, synthetic curves: all three
    output labels (record, curve file, provenance) follow the path."""
    import numpy as np

    class _Path(_StubPath):
        probe_profile = {"name": "selected"}
        regs = {"gain": 1, "ogain": 1}

        def curve(self, freqs, cut_hz, res, amp):
            return np.zeros(len(freqs)), {"regs": self.regs}

        def record(self):
            return {"path": "stub"}
    path = _Path()
    path.engine = engine
    freqs = np.geomspace(100.0, 8000.0, 24)
    meta = {"sha256": "0" * 64, "file": "stub.wav", "rig": None, "commanded": {"cut_hz": 1000.0},
            "cutoff_readback_hz": 1000.0}
    with patch.object(rc, "load_filter_reference",
                      return_value=(freqs, np.zeros(len(freqs)), meta)), \
         patch.object(rc, "dut_probe_grid", return_value=freqs), \
         patch.object(rc, "_legacy_reads", return_value={}), \
         patch.object(rc.rp, "load_profile",
                      return_value={"rigs": {None: {"plugin": {}}},
                                    "built": {"worktree": {"commit": "x"}}}):
        dest = tmp_path / "curve.json"
        if use_default:
            import f1_filter_path as fp
            with patch.object(fp, "SelectedFilterPath", return_value=path):
                rec = rc.run_filter_case(_case(), "", True, response_dest=dest)
        else:
            rec = rc.run_filter_case(_case(), "", True, response_dest=dest,
                                     engine_path=path)
    assert rec["engine"] == engine
    assert rec["provenance"]["engine"] == engine
    assert json.loads(dest.read_text())["engine"] == engine


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


# --- the CLI handler: a cross-module refusal must exit REFUSED (#1643) --------
# `main` guarded `rc.run_filter_case` with `except (rtlpath.Refused, rc.Refused)`.
# The callee reads the frozen reference through
# `run_case.load_filter_reference` -> `refprofile.load_clip`, which raises
# `refprofile.Refused` for an absent, short, mis-hashed or silent clip: a
# SIBLING of both classes the tuple named. On a host without the frozen audio
# cache the refusal left `main` as a traceback (exit 1) -- a refusal rendered
# as FAIL, where the exit contract owes REFUSED (2).
#
# MEASURED, the seven controls against six handler variants (2026-11-28, main @
# a9fe1c53), as failed / passed:
#   the old two-class tuple               3 / 4   absent cache (both flag paths) and
#                                                  mono_m5a_score, the RTL path's
#                                                  model side: each escaped `main`
#   `except Exception`                    2 / 5   ValueError and UnboundLocalError:
#                                                  "DID NOT RAISE"
#   the tuple with `rp.Refused` appended  1 / 6   mono_m5a_score: that repair fixes
#                                                  one sibling, not the family
#   `(rtlpath.Refused, rp.Refused)`       2 / 5   run_case and mono_m5a_score escape
#   the shipped handler, with a LOCAL     7 / 0   the #1624 shape: a local named
#   named `refusal` in `main`                     `refusal` turns every exception
#                                                  leaving the try into an
#                                                  UnboundLocalError; the first
#                                                  control names the local
#   the shipped repair                    0 / 7
#
# WHAT IS STOOD IN, AND WHAT THAT IS BLIND TO. Every control drives the real
# `main` and the real `run_case.run_filter_case`, and the frozen-clip refusal is
# the real `refprofile.load_clip` raise, reached with `refprofile.PROFILE_DIR`
# pointed at an empty directory: the committed profile still names the clip, so
# only its audio is gone -- a host's state before `tools/refprofile_restore.py`.
# Held fixed because they are properties of the HOST, not of the handler:
# `base_check` (this checkout's staleness, which has its own handler earlier in
# `main`) and, in five controls, the RTL path's constructor. That constructor
# needs iverilog/vvp on PATH and builds a model voice first, while `main` reads
# only its `engine` and its iverilog version before the refusal fires; the
# control whose refusal arises INSIDE it runs the real one. Its own refusal
# (`rtlpath.Refused`, exit 2) is controlled in
# tools/test_f1_response_destination.py. Blind to: a constructor that comes to
# refuse for its own reasons before the frozen clip is read. Measured as a real
# process on this host with nothing stood in (Icarus Verilog 13.0, no cache):
# before the repair the CLI exited 1 with a 16-line traceback, after it exit 2
# with the one REFUSED line.

REFUSED_PREFIX = "score_f1_rtl: REFUSED -- "


class _RtlPathStandIn:
    """What `main` reads off the RTL path before `run_filter_case` refuses at
    the frozen clip: its engine (the guard in `run_filter_case`) and its
    iverilog version (the banner). No RTL is simulated."""

    engine = rtlpath.ENGINE
    tools = {"iverilog": "stand-in: no RTL is simulated in these controls"}


def _stale_base_fixed(monkeypatch):
    """`base_check`'s answer is a property of the host's checkout, and it has
    its own handler, before the one under test."""
    monkeypatch.setattr(rc, "BASE_STATE", rc.BASE_STATE)    # `main` rebinds it
    monkeypatch.setattr(rc, "base_check", lambda allow_stale=False: {})


def _rtl_path_stood_in(monkeypatch):
    """Replace the RTL path's constructor, which needs iverilog/vvp on PATH."""
    monkeypatch.setattr(rtlpath, "RtlFilterChainPath", lambda **kw: _RtlPathStandIn())


def _run(tmp_path, *flags):
    results = tmp_path / "results"
    code = score.main(["--case", "F1A", "--results", str(results), *flags])
    return code, results


def _refused_line(out):
    lines = out.err.splitlines()
    assert len(lines) == 1 and lines[0].startswith(REFUSED_PREFIX), lines
    return lines[0]


def _nothing_written(results):
    assert not results.exists() or not any(results.iterdir()), \
        "neither a record nor a curve may be written"


def _frozen_clip():
    return rc.filter_spec(_case()["case_id"])["ref_clip"]


def test_the_refusal_families_the_handler_must_catch_share_only_the_base():
    """The relation the base-class handler rests on: the frozen cache's
    refusal is neither class the tuple named, none is a subclass of another,
    and all three are `refusal.Refused`."""
    assert not issubclass(rp.Refused, (rtlpath.Refused, rc.Refused))
    assert not issubclass(rc.Refused, rtlpath.Refused)
    assert not issubclass(rtlpath.Refused, rc.Refused)
    for klass in (rtlpath.Refused, rc.Refused, rp.Refused):
        assert issubclass(klass, refusal.Refused), klass
    # `except refusal.Refused` reads the MODULE only while no local of `main`
    # is named `refusal`: in f1_level_scaling one was, and the same repair
    # raised UnboundLocalError for every exception leaving the try (#1624).
    assert "refusal" not in score.main.__code__.co_varnames


@pytest.mark.parametrize("flags", [(), ("--no-audio",)],
                         ids=["curve-written", "no-audio"])
def test_absent_frozen_cache_exits_refused_2_and_names_the_clip(tmp_path, monkeypatch,
                                                                capsys, flags):
    """The real raise site. `refprofile.PROFILE_DIR` is pointed at an empty
    directory: the committed profile still names the clip, so only its audio
    is gone -- a host's state before `tools/refprofile_restore.py`."""
    empty = tmp_path / "frozen-cache"
    empty.mkdir()
    monkeypatch.setattr(rp, "PROFILE_DIR", empty)
    _stale_base_fixed(monkeypatch)
    _rtl_path_stood_in(monkeypatch)
    code, results = _run(tmp_path, *flags)
    out = capsys.readouterr()
    assert code == exit_contract.REFUSED, (code, out.out[-400:], out.err[-400:])
    assert "this runs two RTL curves" in out.out, out.out[-400:]
    assert _refused_line(out).startswith(
        f"{REFUSED_PREFIX}the frozen reference audio for {_frozen_clip()!r} "
        "is not in the cache (")
    assert "REFUSED" not in out.out
    _nothing_written(results)


def test_a_run_case_refusal_at_the_same_boundary_still_exits_refused_2(
        tmp_path, monkeypatch, capsys):
    """The sibling the tuple already caught. A repair that swaps the tuple for
    `rp.Refused` fixes the reported escape and drops this one."""
    def not_projectable(clip_id, inject=""):
        raise rc.Refused(f"the frozen clip {clip_id} could not be projected: <control>")
    monkeypatch.setattr(rc, "load_filter_reference", not_projectable)
    _stale_base_fixed(monkeypatch)
    _rtl_path_stood_in(monkeypatch)
    code, results = _run(tmp_path, "--no-audio")
    out = capsys.readouterr()
    assert code == exit_contract.REFUSED, (code, out.err[-400:])
    assert _refused_line(out) == (
        f"{REFUSED_PREFIX}the frozen clip {_frozen_clip()} could not be projected: "
        "<control>")
    _nothing_written(results)


def test_a_mono_m5a_score_refusal_from_the_rtl_path_model_side_exits_refused_2(
        tmp_path, monkeypatch, capsys):
    """The input that defeats the repair the issue rejected -- appending
    `rp.Refused` to the tuple. The REAL constructor builds the model side
    first (`f1_filter_path.SelectedFilterPath`), which asks
    `mono_m5a_score.engine_configuration` for the profile: a third sibling, at
    another line of the same try. It fires only for a profile name missing
    from `ENGINE_PROFILES`, a programming error rather than a host state."""
    import f1_filter_path as fp
    import mono_m5a_score as m5
    monkeypatch.delitem(m5.ENGINE_PROFILES, fp.ENGINE_PROFILE)
    _stale_base_fixed(monkeypatch)
    code, results = _run(tmp_path, "--no-audio")
    out = capsys.readouterr()
    assert code == exit_contract.REFUSED, (code, out.out[-400:], out.err[-400:])
    assert _refused_line(out) == (
        f"{REFUSED_PREFIX}unsupported Mono engine configuration {fp.ENGINE_PROFILE!r}")
    assert "this runs two RTL curves" not in out.out
    _nothing_written(results)


@pytest.mark.parametrize("error", [ValueError, UnboundLocalError])
def test_an_unrelated_error_at_the_same_boundary_is_an_execution_error(
        tmp_path, monkeypatch, capsys, error):
    """The input that defeats the wrong repair (verification-rules rule 8):
    `except Exception` relabels every traceback REFUSED, and these are the
    controls that go red when it is swapped in. `UnboundLocalError` is what the
    #1624 site's first repair turned every refusal into."""
    def not_a_refusal(clip_id, inject=""):
        raise error("<control> the probe grid is not a number")
    monkeypatch.setattr(rc, "load_filter_reference", not_a_refusal)
    _stale_base_fixed(monkeypatch)
    _rtl_path_stood_in(monkeypatch)
    results = tmp_path / "results"
    with pytest.raises(error, match="the probe grid is not a number") as err:
        score.main(["--case", "F1A", "--results", str(results), "--no-audio"])
    assert "run_filter_case" in [fr.name for fr in err.traceback], \
        "the control must fire at the frozen-clip boundary"
    out = capsys.readouterr()
    assert "REFUSED" not in out.err, \
        "an execution error must not be relabelled a refusal"
    _nothing_written(results)
