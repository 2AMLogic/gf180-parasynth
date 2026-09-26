"""The F1 `integrated-rtl` gate: a record must support the engine it claims.

These are the cheap checks, and they are the ones that catch the failure this
anchor is most exposed to: `engine` is free text, so a hand-edited or
half-migrated record can claim the instrument while carrying model numbers.
"""
import copy
import hashlib
import json
import pathlib

import pytest

import check_f1_rtl_record as gate

ROOT = pathlib.Path(gate.ROOT)


def _tree_hashes():
    return {rel: hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()[:16]
            for rel in gate.BOUND_SOURCES}


def _record(**over):
    record = {
        "engine": "integrated-rtl",
        "case_id": "F1A",
        "provenance": {"engine": "integrated-rtl", "config": {}},
        "metrics": {"Corner frequency": {"value": 108.0, "valid": True}},
        "model_path": {"rtl_chain": {
            "chain_version": "f1-rtl-filter-chain-v1",
            "bench": "rtl-sketch/tb_f1_chain.v",
            "simulator": "iverilog",
            "simulator_versions": {"iverilog": "Icarus Verilog version 13.0 (stable)"},
            "rtl_injection": None,
            "frames_limit": None,
            "source_sha256": _tree_hashes(),
            "curves": [
                {"tag": "cut250-res0p0", "cut_hz": 250.0,
                 "words_driven": {"g": 1089, "k_eff": 0, "gain": 42598, "ogain": 100825},
                 "rtl_vs_model": {"frames": 442296, "mismatches": 0, "bit_exact": True}},
                {"tag": "cut20000-res0p0", "cut_hz": 20000.0,
                 "words_driven": {"g": 61659, "k_eff": 0, "gain": 42598, "ogain": 100825},
                 "rtl_vs_model": {"frames": 442296, "mismatches": 0, "bit_exact": True}},
            ]}},
        "engine_comparison": {"rtl_vs_model_metrics": {
            "twin_present": True, "twin_engine": "fixed-model", "agrees": True,
            "metrics_that_moved": [], "metrics": {"Corner frequency": {"same": True}}}},
    }
    record.update(over)
    return record


def test_accepts_a_record_whose_rtl_chain_matches_the_tree():
    summary = gate.check_record(_record())
    assert summary["engine"] == "integrated-rtl"
    assert summary["curves"] == 2 and summary["bit_exact_against_model"] is True
    assert summary["twin_engine"] == "fixed-model"


@pytest.mark.parametrize("engine", ["fixed-model", "", None, "integrated-RTL", "rtl"])
def test_rejects_a_record_whose_engine_is_not_integrated_rtl(engine):
    record = _record(engine=engine)
    with pytest.raises(gate.Rejected, match="not 'integrated-rtl'"):
        gate.check_record(record)


def test_rejects_a_record_whose_provenance_still_says_fixed_model():
    """The headline and the provenance disagreeing about what produced the audio
    is the exact shape of record #94 was filed about."""
    record = _record()
    record["provenance"]["engine"] = "fixed-model"
    with pytest.raises(gate.Rejected, match="provenance names engine"):
        gate.check_record(record)


def test_rejects_a_record_with_no_rtl_chain_block():
    record = _record(model_path={})
    with pytest.raises(gate.Rejected, match="no model_path.rtl_chain"):
        gate.check_record(record)


def test_rejects_a_control_and_a_smoke_run():
    with pytest.raises(gate.Rejected, match="a control"):
        gate.check_record(_record(INJECTED_CONTROL="RTL:F1_CHAIN_DROP_DECIM"))
    with pytest.raises(gate.Rejected, match="smoke run"):
        gate.check_record(_record(SMOKE_RUN="stimulus truncated to 30000 frames"))
    record = _record()
    record["model_path"]["rtl_chain"]["rtl_injection"] = "LADDER_FB"
    with pytest.raises(gate.Rejected, match="carried injection"):
        gate.check_record(record)
    record = _record()
    record["model_path"]["rtl_chain"]["frames_limit"] = 30000
    with pytest.raises(gate.Rejected, match="truncated"):
        gate.check_record(record)


def test_rejects_a_record_whose_rtl_has_changed_since():
    record = _record()
    hashes = record["model_path"]["rtl_chain"]["source_sha256"]
    hashes["rtl-sketch/ladder_dp_n.v"] = "0" * 16
    with pytest.raises(gate.Rejected, match="the RTL has changed since"):
        gate.check_record(record)


def test_rejects_a_record_that_names_no_simulator_build():
    record = _record()
    record["model_path"]["rtl_chain"]["simulator_versions"] = {}
    with pytest.raises(gate.Rejected, match="which simulator build"):
        gate.check_record(record)
    record = _record()
    record["model_path"]["rtl_chain"]["simulator"] = "the model"
    with pytest.raises(gate.Rejected, match="not a known RTL simulator"):
        gate.check_record(record)


def test_rejects_one_curve_and_a_curve_with_no_stream_comparison():
    record = _record()
    record["model_path"]["rtl_chain"]["curves"] = \
        record["model_path"]["rtl_chain"]["curves"][:1]
    with pytest.raises(gate.Rejected, match="curves recorded"):
        gate.check_record(record)
    record = _record()
    del record["model_path"]["rtl_chain"]["curves"][1]["rtl_vs_model"]
    with pytest.raises(gate.Rejected, match="no model-versus-RTL stream comparison"):
        gate.check_record(record)


def test_rejects_a_record_that_drops_the_twin_comparison():
    record = _record()
    del record["engine_comparison"]
    with pytest.raises(gate.Rejected, match="no engine_comparison"):
        gate.check_record(record)


def test_a_mismatching_record_is_accepted_but_says_so():
    """A disagreement with the model is a finding, not grounds for rejection:
    the gate checks provenance, not that the RTL agrees."""
    record = _record()
    record["model_path"]["rtl_chain"]["curves"][0]["rtl_vs_model"] = {
        "frames": 442296, "mismatches": 17, "bit_exact": False}
    summary = gate.check_record(record)
    assert summary["mismatches"] == 17
    assert summary["bit_exact_against_model"] is False


def test_the_board_record_passes_the_gate_if_it_claims_the_rtl():
    """The F1 record on the board: if (and only if) it says integrated-rtl, it
    must survive this gate in the tree it is read in."""
    path = ROOT / "docs/scorecard/results/F1A.json"
    record = json.loads(path.read_text())
    if record.get("engine") != "integrated-rtl":
        pytest.skip("the board's F1A record is not an RTL anchor in this tree")
    assert gate.check_record(record)["case_id"] == "F1A"


def test_the_gate_cli_rejects_a_fixed_model_record(tmp_path):
    record = _record(engine="fixed-model")
    record["provenance"]["engine"] = "fixed-model"
    dest = tmp_path / "F1A.json"
    dest.write_text(json.dumps(record))
    assert gate.main([str(dest)]) == 1
    assert gate.main([str(tmp_path / "missing.json")]) == 2
    good = tmp_path / "good.json"
    good.write_text(json.dumps(_record()))
    assert gate.main([str(good)]) == 0


def test_bound_sources_exist_so_the_gate_is_satisfiable():
    """A gate written against a file that does not exist is worse than no gate."""
    for rel in gate.BOUND_SOURCES:
        assert (ROOT / rel).is_file(), rel
    assert copy.deepcopy(_tree_hashes())
