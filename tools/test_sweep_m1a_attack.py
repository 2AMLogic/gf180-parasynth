"""The sweep's gates must be able to fail (controls on the committed record)."""
import copy
import json
import os
import shutil
from pathlib import Path

import pytest
import sweep_m1a_attack as sweep

IN_DOMAIN = dict(shape_p=1.0, ramp_ms=3.0)
FLOOR = dict(shape_p=4.0, ramp_ms=32 * 1000 / 48000)


def _selected_as_row():
    record = json.loads(sweep.RECORD.read_text())
    row = {"attack_ms": 10.0, "patch_id": "x", "sha256": "x", "components": {},
           "measurements": copy.deepcopy({k: record["diagnostics"][k]
                                          for k in ("properties", "events")})}
    return record, row


def _fits(row, model=None, reference=None):
    for e in row["measurements"]["events"]:
        if model:
            e["attack_fit"]["model"].update(model)
        if reference:
            e["attack_fit"]["reference"].update(reference)
    return row


def _fast(row):
    for e in row["measurements"]["events"]:
        e["attack_10_90_ms"]["model"] = e["attack_10_90_ms"]["reference"] + 4.9
    return row


def test_selected_patch_is_unqualified_on_its_reference_fits():
    """M1A's committed reference fits (p=3; p=4 on the search minimum) are
    out of domain, so even the selected patch's attack is not gradeable."""
    record, row = _selected_as_row()
    out = sweep.evaluate(row, record)
    assert out["preserved"] and out["attack_state"] == "unqualified"
    assert not out["reference_fits_in_qualified_domain"] and not out["candidate"]
    raw = record["diagnostics"]["properties"]["Envelope attack"]["unqualified_error"]
    assert out["attack_raw_worst_error_ms"] == pytest.approx(raw, abs=1e-4)


def test_both_sides_in_domain_grade_and_preservation_blocks():
    record, row = _selected_as_row()
    fast = _fits(_fast(copy.deepcopy(row)), model=IN_DOMAIN, reference=IN_DOMAIN)
    assert sweep.evaluate(fast, record)["candidate"]
    lossy = copy.deepcopy(fast)
    lossy["measurements"]["properties"]["Pitch"]["error"] = 2.0
    assert sweep.evaluate(lossy, record)["lost_property_passes"] == ["Pitch"]
    assert not sweep.evaluate(lossy, record)["candidate"]
    partial = copy.deepcopy(fast)
    ev = partial["measurements"]["events"][0]["harmonic_error_db_model_minus_reference"]
    key = next(k for k, v in ev.items() if v is not None and abs(v) <= 1)
    ev[key] = 5.0
    assert not sweep.evaluate(partial, record)["candidate"]
    edge = copy.deepcopy(fast)
    edge["measurements"]["events"][1]["attack_10_90_ms"]["model"] = \
        edge["measurements"]["events"][1]["attack_10_90_ms"]["reference"] - 5.01
    assert sweep.evaluate(edge, record)["attack_state"] == "fail"


@pytest.mark.parametrize("side", ["model", "reference"])
def test_out_of_domain_on_either_side_is_unqualified(side):
    record, row = _selected_as_row()
    fits = {"model": IN_DOMAIN, "reference": IN_DOMAIN, side: FLOOR}
    out = sweep.evaluate(_fits(_fast(copy.deepcopy(row)), **fits), record)
    assert out["raw_errors_within_limit"] and out["preserved"]
    assert out["attack_state"] == "unqualified" and not out["candidate"]
    # exploratory only when the MODEL side is qualified
    assert out["exploratory"] == (side == "reference")


def test_off_grid_value_refuses():
    with pytest.raises(sweep.bass.Refused, match="grid"):
        sweep.render_point(7.0)
    assert sweep.nominal_vca_10_90_ms(10.0) == pytest.approx(8.0)


@pytest.mark.parametrize("interrupt", [True, False], ids=["interrupted", "successful"])
def test_summarize_publishes_one_complete_report(tmp_path, monkeypatch, interrupt):
    shutil.copytree(sweep.OUT, tmp_path, dirs_exist_ok=True)
    monkeypatch.setattr(sweep, "OUT", tmp_path)
    target = tmp_path / "report.json"
    previous = target.read_bytes()
    field = "reference_fits_in_qualified_domain"
    replace = os.replace
    publications = []

    def replace_report(src, dst):
        assert Path(dst) == target
        staged = json.loads(Path(src).read_bytes())
        # Allow any premature publication. Interrupt just before the complete
        # report becomes visible, even if an earlier rename has succeeded.
        if interrupt and field in staged:
            raise OSError("injected interruption before publishing complete report")
        replace(src, dst)
        publications.append(staged)

    monkeypatch.setattr(os, "replace", replace_report)
    if interrupt:
        with pytest.raises(OSError, match="injected interruption"):
            sweep.summarize()
        assert target.read_bytes() == previous
        assert not publications
    else:
        sweep.summarize()
        report = json.loads(target.read_bytes())
        assert len(publications) == 1
        assert publications[0] == report
        expected = json.loads(previous)
        assert report.keys() == expected.keys()
        assert report[field] == expected[field]
