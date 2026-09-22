"""Board identity and resource checks, independent of sound correctness."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

import icepi_preflight as check


ROOT = Path(__file__).resolve().parents[1]
PUBLICATION = ROOT / "fpga/reports/selected/linux-85f/publication.json"


def test_real_85f_publication_cannot_be_used_on_icepi():
    result = check.assess_publication(json.loads(PUBLICATION.read_text()))
    assert result["state"] == "INCOMPATIBLE"
    assert result["resource_excess"]["MULT18X18D"] == 76
    assert result["resource_excess"]["TRELLIS_COMB"] == 6518
    assert {"device", "package", "board_constraints"} <= set(result["identity_mismatches"])


def compatible_record():
    record = json.loads(PUBLICATION.read_text())
    record.update(device="25k", package="CABGA256", board="icepi-zero")
    record["source_sha256"] = {"fpga/boards/icepi-zero-v1.3.lpf": "1" * 64}
    for resource in ("MULT18X18D", "TRELLIS_COMB"):
        record["timing"]["utilization"][resource]["used"] = 1
    return record


def test_capacity_screen_is_not_a_bitstream_or_sound_qualification():
    result = check.assess_publication(compatible_record())
    assert result["state"] == "REQUIRES_BOARD_VERIFICATION"
    assert result["ready_to_program"] is False


@pytest.mark.parametrize("bad", [None, -1, float("nan"), 1.5, True])
def test_unknown_or_malformed_utilization_refuses(bad):
    record = compatible_record()
    record["timing"]["utilization"]["MULT18X18D"]["used"] = bad
    with pytest.raises(ValueError, match="utilization"):
        check.assess_publication(record)


def test_missing_resource_and_unqualified_build_refuse():
    record = compatible_record()
    del record["timing"]["utilization"]["MULT18X18D"]
    with pytest.raises(ValueError, match="utilization"):
        check.assess_publication(record)
    record = compatible_record()
    record["bitstream_qualified"] = False
    with pytest.raises(ValueError, match="qualified"):
        check.assess_publication(record)


def test_clock_pin_requires_board_revision():
    assert check.pin_plan("1.2")["clock"]["site"] == "M2"
    assert check.pin_plan("1.3")["clock"]["site"] == "M1"
    assert check.pin_plan("1.4")["clock"]["site"] == "M1"
    for revision in (None, "1.1", "unknown"):
        with pytest.raises(ValueError, match="revision"):
            check.pin_plan(revision)
    assert check.pin_plan("1.4")["signals"]["i2s_sdata"] == {
        "gpio": 21, "header_pin": 40, "site": "F2", "direction": "output"}


def test_known_netlist_counts_every_multiplier_once(tmp_path):
    cells = {
        "u_synth.u_voice.osc2_path.p0.mul": {"type": "MULT18X18D"},
        "u_synth.u_voice.u_ladder.mul": {"type": "MULT18X18D"},
        "u_synth.u_drums.src.mul": {"type": "MULT18X18D"},
        "flattened_filter": {"type": "MULT18X18D", "attributes": {
            "src": "/some/path/rtl-sketch/rate_conv_2x.v:147.42-147.84|dsp_map.v:1"}},
        "u_synth.u_voice.mr": {"type": "MULT18X18D"},
        "other": {"type": "MULT18X18D"},
        "not_a_multiplier": {"type": "LUT4"},
    }
    netlist = tmp_path / "netlist.json"
    netlist.write_text(json.dumps({"modules": {"ulx3s_top": {"cells": cells}}}))
    digest = hashlib.sha256(netlist.read_bytes()).hexdigest()
    result = check.count_dsp(netlist, digest)
    assert result["total"] == 6
    assert result["by_block"] == {"rate_converter": 1, "oscillator_2x": 1,
                                  "ladder": 1, "drums": 1, "other_voice": 1,
                                  "unattributed": 1}
    with pytest.raises(ValueError, match="hash"):
        check.count_dsp(netlist, "0" * 64)
    wrong_top = tmp_path / "empty.json"
    wrong_top.write_text(json.dumps({"modules": {}}))
    with pytest.raises(ValueError, match="top"):
        check.count_dsp(wrong_top, hashlib.sha256(wrong_top.read_bytes()).hexdigest())


def test_removing_mutation_leaves_compatible_capacity_screen():
    record = compatible_record()
    changed = copy.deepcopy(record)
    changed["timing"]["utilization"]["MULT18X18D"]["used"] = 29
    assert check.assess_publication(changed)["state"] == "INCOMPATIBLE"
    assert check.assess_publication(record)["state"] == "REQUIRES_BOARD_VERIFICATION"
