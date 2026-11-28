import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import external_claim as ec

ENV = dict(host="dawdreamer", host_version="0.9.0", loader="vst3", machine="mac-a",
           binary_sha256="ab" * 32, block_size=512, sample_rate=48000,
           licence_state="licensed", preset="init")


def neg():
    e2 = dict(ENV, host="pedalboard", host_version="0.9.x")
    return dict(id="x", polarity="negative", environment=ENV,
                second_route=dict(environment=e2, result="negative"))


def test_complete_positive_ok():
    assert ec.check_claim(dict(polarity="positive", environment=ENV)) == []


def test_missing_env_key_refused():
    for k in ec.ENV_KEYS:  # injected defect: drop each key in turn
        env = {a: b for a, b in ENV.items() if a != k}
        assert ec.check_claim(dict(polarity="positive", environment=env)), k


def test_negative_needs_second_route():
    c = neg(); del c["second_route"]
    assert any("second_route" in p for p in ec.check_claim(c))
    assert ec.check_claim(neg()) == []


def test_second_route_must_differ_and_agree():
    c = neg(); c["second_route"]["environment"] = copy.deepcopy(ENV)
    assert any("different" in p for p in ec.check_claim(c))
    c = neg(); c["second_route"]["result"] = "positive"
    assert any("reproduce" in p for p in ec.check_claim(c))


def test_readback_octave_down_and_silence_and_pin():
    assert ec.assert_readback(expected_hz=261.63, measured_hz=261.0, level_dbfs=-12) == []
    assert any("pitch" in f for f in ec.assert_readback(expected_hz=261.63, measured_hz=131.0, level_dbfs=-12))
    assert any("level" in f for f in ec.assert_readback(expected_hz=261.63, measured_hz=261.6, level_dbfs=-120))
    assert any("pin" in f for f in ec.assert_readback(expected_hz=1, measured_hz=1, level_dbfs=-6,
                                                     pins_set={"range": 3.0}, pins_read={"range": 2.0}))


def test_committed_data_passes():
    assert ec.main() == 0


def _write(tmp_path, data):
    p = tmp_path / "claims.json"
    p.write_text(json.dumps(data))
    return p


def test_main_fails_on_bad_committed_record(tmp_path):
    # Control for the data gate: the same main() that passes the committed
    # file must go red on a judgeable but non-compliant record.
    c = neg(); del c["second_route"]
    assert ec.main(_write(tmp_path, {"claims": [c]})) == 1
    c = dict(id="p", polarity="positive", environment={k: v for k, v in ENV.items() if k != "preset"})
    assert ec.main(_write(tmp_path, {"claims": [c]})) == 1
    assert ec.main(_write(tmp_path, {"claims": [neg()]})) == 0


def test_main_refuses_record_without_polarity_or_environment(tmp_path):
    for drop in ("polarity", "environment"):
        c = {k: v for k, v in neg().items() if k != drop}
        assert ec.main(_write(tmp_path, {"claims": [c]})) == 2, drop
    assert ec.main(_write(tmp_path, {"claims": ["not a record"]})) == 2
    assert ec.main(tmp_path / "absent.json") == 2


def test_route_differing_only_by_omission_refused():
    for k in ("loader", "machine"):
        c = neg()
        c["second_route"]["environment"] = {a: b for a, b in ENV.items() if a != k}
        assert any(f"environment.{k}" in p for p in ec.check_claim(c)), k


def test_host_table_unbacked_fact_fails_and_consumer_refuses(tmp_path):
    table = {"Model D": {"dawdreamer 0.9.0": {"observed": "silent", "status": "verified",
                                              "claim": "missing"}}}
    p = _write(tmp_path, {"claims": [], "host_per_plugin": table})
    assert ec.main(p) == 1
    table["Model D"]["dawdreamer 0.9.0"] = {"observed": "silent"}  # no status
    assert ec.main(_write(tmp_path, {"claims": [], "host_per_plugin": table})) == 1
    table["Model D"]["dawdreamer 0.9.0"] = {"observed": "silent", "status": "unverified"}
    p = _write(tmp_path, {"claims": [], "host_per_plugin": table})
    assert ec.main(p) == 0
    with pytest.raises(ec.Unverified):
        ec.host_result("Model D", "dawdreamer 0.9.0", p)
    table["Model D"]["dawdreamer 0.9.0"] = {"observed": "silent", "status": "verified", "claim": "x"}
    p = _write(tmp_path, {"claims": [neg()], "host_per_plugin": table})
    assert ec.main(p) == 0
    assert ec.host_result("Model D", "dawdreamer 0.9.0", p) == "silent"


def test_committed_host_table_is_not_consumable_as_fact():
    with pytest.raises(ec.Unverified):
        ec.host_result("Model D", "dawdreamer 0.9.0")


@pytest.mark.parametrize("bad", [True, "x", [1], 1])
def test_malformed_truthy_second_route_is_a_problem_not_a_crash(bad):
    c = neg()
    c["second_route"] = bad
    assert "second_route is not an object" in ec.check_claim(c)


@pytest.mark.parametrize("falsy", [[], 0, "", False, None])
def test_falsy_second_route_still_reads_as_missing(falsy):
    c = neg()
    c["second_route"] = falsy
    assert ec.check_claim(c) == ["negative claim has no second_route"]


def test_main_types_malformed_second_route_without_traceback(tmp_path, capsys):
    c = neg()
    c["second_route"] = True
    assert ec.main(_write(tmp_path, {"claims": [c]})) == 1
    out = capsys.readouterr().out
    assert "FAIL" in out and "second_route is not an object" in out
    assert "Traceback" not in out


NAN = float("nan")


def test_readback_passes_when_every_operand_is_a_finite_number():
    """The control for the refusals below (#1994). They exist because IEEE
    comparisons with NaN are always False (#134): `level_dbfs < min_level_dbfs`
    let a NaN level through "silence is not data", and `abs(got - want) > 1e-6`
    let a NaN read back as the pin that was set. The rule-6 readback is the one
    place a plugin's state is asserted rather than assumed, so each operand is
    asserted to be a finite number first."""
    assert ec.assert_readback(expected_hz=261.63, measured_hz=261.0, level_dbfs=-12,
                              pins_set={"range": 3.0, "oct": 1.0},
                              pins_read={"range": 3.0, "oct": 1.0}) == []


@pytest.mark.parametrize("level", [NAN, float("inf"), -float("inf")])
def test_readback_fails_a_level_that_is_not_a_finite_number(level):
    fails = ec.assert_readback(expected_hz=261.63, measured_hz=261.0, level_dbfs=level)
    assert len(fails) == 1, fails
    assert fails[0].startswith("level:") and "not a finite number" in fails[0], fails
    assert "silence is not data" in fails[0]


def test_readback_fails_a_level_floor_that_is_not_a_finite_number():
    for bad in (NAN, float("inf"), -float("inf")):
        fails = ec.assert_readback(expected_hz=261.63, measured_hz=261.0, level_dbfs=-12,
                                   min_level_dbfs=bad)
        assert fails == [f"min_level_dbfs: {bad} is not a finite number"], bad


@pytest.mark.parametrize("bad", [NAN, float("inf"), -float("inf")])
def test_readback_fails_a_pin_read_back_as_a_non_finite_number(bad):
    fails = ec.assert_readback(expected_hz=1, measured_hz=1, level_dbfs=-6,
                               pins_set={"range": 3.0, "oct": 1.0}, pins_read={"range": bad, "oct": 1.0})
    assert fails == [f"pin range: read back {bad}, which is not a finite number"]


@pytest.mark.parametrize("bad", [NAN, float("inf"), -float("inf")])
def test_readback_fails_a_pin_set_to_a_non_finite_number(bad):
    fails = ec.assert_readback(expected_hz=1, measured_hz=1, level_dbfs=-6,
                               pins_set={"range": bad}, pins_read={"range": 3.0})
    assert fails == [f"pin range: set {bad}, which is not a finite number"]


def test_readback_names_each_non_finite_operand_and_withholds_the_comparison():
    """Both operands bad at once: each is named, and no 'below' or 'read back'
    line is derived from an operand the rule has just declared unusable."""
    fails = ec.assert_readback(expected_hz=1, measured_hz=1, level_dbfs=NAN, min_level_dbfs=float("inf"),
                               pins_set={"range": NAN}, pins_read={"range": float("inf")})
    assert fails == [
        "level: nan dBFS is not a finite number (silence is not data)",
        "min_level_dbfs: inf is not a finite number",
        "pin range: read back inf, which is not a finite number",
        "pin range: set nan, which is not a finite number"]


def test_readback_still_reports_an_absent_level_and_an_absent_pin_as_failures():
    """`None` is "no reading", not a NaN: both stay the failures they were."""
    fails = ec.assert_readback(expected_hz=1, measured_hz=1, level_dbfs=None,
                               pins_set={"range": 3.0}, pins_read={})
    assert fails == ["level: None dBFS below -60.0 (silence is not data)",
                     "pin range: set 3.0, read back None"]
