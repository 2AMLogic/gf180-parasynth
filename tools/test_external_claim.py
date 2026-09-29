import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import external_claim as ec

ENV = dict(host="dawdreamer", host_version="0.9.0", binary_sha256="ab" * 32, block_size=512,
           sample_rate=48000, licence_state="licensed", preset="init")


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
