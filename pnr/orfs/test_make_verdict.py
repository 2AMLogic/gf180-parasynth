"""Controls for the S1 verdict deriver.

A verdict file is only worth reading if the thing that produced it fails on the
inputs it is supposed to fail on. These are the injected defects: a non-zero DRC
count, a negative slack, an absent metric, a hand-edited verdict.
"""
from __future__ import annotations
import json, pathlib, shutil, sys
import pytest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import make_verdict as mv                                            # noqa: E402

S1 = HERE / "evidence" / "synth_top" / "joined-d1e5068"


# --------------------------------------------------------------- the real run
def test_the_committed_verdict_matches_the_committed_metrics():
    """Rule 8's defeating input for a `passed` key: somebody types it.

    `--check` is the guard, and this runs it against the committed tree, so a
    hand-edited verdict.json fails here rather than rendering S1 green.
    """
    assert mv.main([str(S1), "--check"]) == 0


def test_the_committed_run_passes_and_says_what_it_checked():
    v = mv.derive(str(S1))
    assert v["passed"] is True
    assert all(c["ok"] for c in v["checks"])
    # The whole point: DRC comes from the ROUTE stage, not from 6_report.json,
    # which carries no DRC metric at all.
    drc = [c for c in v["checks"] if c["metric"] == "detailedroute__route__drc_errors"]
    assert drc and drc[0]["source"] == "5_2_route.json"
    assert "drc" not in json.dumps(json.load(open(S1 / "6_report.json"))).lower()


def test_max_cap_is_disclosed_rather_than_dropped():
    """It is 2, not 0. A verdict that omitted it would be the quieter lie."""
    v = mv.derive(str(S1))
    mc = {d["metric"]: d["value"] for d in v["disclosed_not_gating"]}
    assert mc["finish__timing__drv__max_cap"] == 2
    assert v["disclosed_reason"]


def test_it_states_what_it_does_not_establish():
    v = mv.derive(str(S1))
    joined = " ".join(v["not_established_by_this_verdict"]).lower()
    assert "sign-off drc" in joined and "lvs" in joined


# ------------------------------------------------------- injected defects
@pytest.fixture
def copy(tmp_path):
    def _copy(**patch):
        d = tmp_path / "ev"
        shutil.copytree(S1, d)
        for fname, edits in patch.items():
            p = d / fname
            doc = json.load(open(p))
            for k, v in edits.items():
                if v is mv:                      # sentinel: delete the key
                    doc.pop(k, None)
                else:
                    doc[k] = v
            p.write_text(json.dumps(doc))
        return d
    return _copy


def test_a_nonzero_router_drc_count_fails(copy):
    """The count 6_report.json cannot see. Iteration 0 of this very run had
    17,929 of them, so this is the run's own defeating input."""
    d = copy(**{"5_2_route.json": {"detailedroute__route__drc_errors": 17929}})
    v = mv.derive(str(d))
    assert v["passed"] is False and "17929" in v["detail"]


def test_an_antenna_violation_fails(copy):
    d = copy(**{"5_2_route.json": {"detailedroute__antenna__violating__nets": 2}})
    assert mv.derive(str(d))["passed"] is False


def test_negative_setup_slack_fails(copy):
    d = copy(**{"6_report.json": {"finish__timing__setup__ws": -0.4}})
    v = mv.derive(str(d))
    assert v["passed"] is False and "-0.4" in v["detail"]


def test_a_setup_violation_count_fails(copy):
    d = copy(**{"6_report.json": {"finish__timing__drv__setup_violation_count": 3}})
    assert mv.derive(str(d))["passed"] is False


def test_an_absent_metric_refuses_rather_than_passing(copy):
    """The input that defeats a `.get(key, 0)` deriver: delete the key. An
    absent DRC count must never read as a zero DRC count."""
    d = copy(**{"5_2_route.json": {"detailedroute__route__drc_errors": mv}})
    with pytest.raises(mv.Refusal) as e:
        mv.derive(str(d))
    assert "detailedroute__route__drc_errors" in str(e.value)
    assert mv.main([str(d), "--print"]) == 2


def test_a_metric_that_is_not_a_number_refuses(copy):
    d = copy(**{"6_report.json": {"finish__timing__setup__ws": "43.5918"}})
    with pytest.raises(mv.Refusal):
        mv.derive(str(d))


def test_a_missing_stage_file_refuses(tmp_path):
    (tmp_path / "6_report.json").write_text("{}")
    with pytest.raises(mv.Refusal) as e:
        mv.derive(str(tmp_path))
    assert "5_2_route.json" in str(e.value)


def test_a_malformed_stage_file_refuses_not_fails(tmp_path):
    shutil.copytree(S1, tmp_path / "ev")
    (tmp_path / "ev" / "6_report.json").write_text("{truncated")
    with pytest.raises(mv.Refusal) as e:
        mv.derive(str(tmp_path / "ev"))
    assert "unreadable" in str(e.value)


def test_a_verdict_copied_from_another_run_fails_check(copy):
    """The copied verdict.json still names `joined-d1e5068`, so --check rejects
    it even though every number in it is true -- of a different directory."""
    d = copy()
    assert json.load(open(d / "verdict.json"))["evidence_dir"] == "joined-d1e5068"
    assert mv.main([str(d), "--check"]) == 1


def test_check_catches_a_hand_edited_verdict(copy):
    d = copy()
    assert mv.main([str(d)]) == 0            # derive this directory's own verdict
    assert mv.main([str(d), "--check"]) == 0
    v = json.load(open(d / "verdict.json"))
    v["passed"] = True
    v["checks"][0]["value"] = 999            # a lie the key check cannot see
    (d / "verdict.json").write_text(json.dumps(v, indent=2, sort_keys=True) + "\n")
    assert mv.main([str(d), "--check"]) == 1


def test_check_reports_an_absent_verdict_rather_than_writing_one(copy):
    d = copy()
    (d / "verdict.json").unlink()
    assert mv.main([str(d), "--check"]) == 1
    assert not (d / "verdict.json").exists()
