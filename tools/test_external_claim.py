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
