"""The sweep's gates must be able to fail (controls on the committed record)."""
import copy
import json

import pytest
import sweep_m1a_attack as sweep


def _selected_as_row():
    record = json.loads(sweep.RECORD.read_text())
    row = {"attack_ms": 10.0, "patch_id": "x", "sha256": "x", "components": {},
           "measurements": copy.deepcopy({k: record["diagnostics"][k]
                                          for k in ("properties", "events")})}
    return record, row


def test_selected_patch_is_preserved_and_fails_attack():
    record, row = _selected_as_row()
    out = sweep.evaluate(row, record)
    assert out["preserved"] and not out["attack_pass"] and not out["candidate"]
    assert out["attack_property_error_ms"] == pytest.approx(8.10889)


def test_controls_turn_gates_red_and_green():
    record, row = _selected_as_row()
    # every event's model attack moved onto the reference: attack passes
    fast = copy.deepcopy(row)
    for e in fast["measurements"]["events"]:
        e["attack_10_90_ms"]["model"] = e["attack_10_90_ms"]["reference"] + 4.9
    assert sweep.evaluate(fast, record)["candidate"]
    # ...but losing the Pitch pass or one partial pass must block the candidate
    lossy = copy.deepcopy(fast)
    lossy["measurements"]["properties"]["Pitch"]["error"] = 2.0
    assert sweep.evaluate(lossy, record)["lost_property_passes"] == ["Pitch"]
    assert not sweep.evaluate(lossy, record)["candidate"]
    partial = copy.deepcopy(fast)
    ev = partial["measurements"]["events"][0]["harmonic_error_db_model_minus_reference"]
    key = next(k for k, v in ev.items() if v is not None and abs(v) <= 1)
    ev[key] = 5.0
    assert not sweep.evaluate(partial, record)["candidate"]
    # one event at 5.01 ms is outside the limit
    edge = copy.deepcopy(fast)
    edge["measurements"]["events"][1]["attack_10_90_ms"]["model"] = \
        edge["measurements"]["events"][1]["attack_10_90_ms"]["reference"] - 5.01
    assert not sweep.evaluate(edge, record)["attack_pass"]


def test_off_grid_value_refuses():
    with pytest.raises(sweep.bass.Refused, match="grid"):
        sweep.render_point(7.0)
