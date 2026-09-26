"""Tests for the X-netlist area refusal (issue #245, AC1).

These run without yosys or iverilog on purpose: they pin the DECISION logic and
the receipt logic, which is where a control silently stops being a control. The
end-to-end run is `make controls`' job -- see the Makefile -- because a test that
skips when a tool is absent is a green that checked nothing, and the aggregate
control target REFUSES instead.
"""
from __future__ import annotations

import pathlib
import shutil
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import report_synth_area as rsa  # noqa: E402

PORTS = {
    "clk": {"direction": "input", "width": 1, "signed": False, "bits": [2]},
    "rst_n": {"direction": "input", "width": 1, "signed": False, "bits": [3]},
    "v": {"direction": "input", "width": 4, "signed": True, "bits": [4, 5, 6, 7]},
    "y": {"direction": "output", "width": 2, "signed": True, "bits": [8, 9]},
}


# ------------------------------------------------------ yosys constant decoding

@pytest.mark.parametrize("text,value", [
    ("s32'00000000000000000000000000000101", 5),
    ("32'00000000000000000000000000000100", 4),
    ("00000000000000000000000000000100", 4),
    ("s32'11111111111111111111111111111111", -1),
    ("tanh16.hex", "tanh16.hex"),
])
def test_yosys_constants_decode(text, value):
    assert rsa.decode_yosys_const(text) == value


def test_a_paramod_module_name_is_the_parameter_receipt():
    name = "$paramod\\tanh_dp\\IDX_BITS=s32'00000000000000000000000000000101"
    assert rsa.paramod_params(name) == ("tanh_dp", {"IDX_BITS": 5})


def test_a_plain_module_name_is_not_a_paramod():
    assert rsa.paramod_params("tanh_dp") is None


def test_the_yosys_receipt_refuses_when_the_override_never_reached_synthesis():
    names = ["tanh_dp_cfg", "$paramod\\tanh_dp\\IDX_BITS=s32'" + "0" * 29 + "100"]
    with pytest.raises(rsa.Refused) as caught:
        rsa.yosys_parameter_receipt(names, "tanh_dp", {"IDX_BITS": 5})
    assert caught.value.reason == "injection-not-active"


def test_the_yosys_receipt_accepts_the_module_it_asked_for():
    names = ["tanh_dp_cfg", "$paramod\\tanh_dp\\IDX_BITS=s32'" + "0" * 29 + "101"]
    assert rsa.yosys_parameter_receipt(names, "tanh_dp", {"IDX_BITS": 5}) == {
        "IDX_BITS": 5}


# ------------------------------------------------------------ generated source

def test_the_wrapper_carries_the_override_for_both_tools():
    text = rsa.wrapper_source("tanh_dp", PORTS, {"IDX_BITS": 5})
    assert "tanh_dp #(.IDX_BITS(5))" in text
    assert "output wire signed [1:0] y" in text
    assert "input wire signed [3:0] v" in text


def test_the_bench_checks_every_output_for_x_and_prints_its_receipts():
    text = rsa.tb_source("tanh_dp", PORTS, "clk", "rst_n", True, {"IDX_BITS": 5}, 32)
    assert "if (^y === 1'bx)" in text
    assert '$display("PARAM IDX_BITS %0d", dut.u.IDX_BITS);' in text
    assert '$display("PORTX y %0d", x_y);' in text
    assert "cyc < 32" in text
    assert "rst_n = 1'b0;" in text and "rst_n = 1'b1;" in text
    assert ".clk(clk)" in text


def test_an_active_high_reset_is_driven_the_other_way():
    ports = dict(PORTS, rst=PORTS["rst_n"])
    del ports["rst_n"]
    text = rsa.tb_source("d", ports, "clk", "rst", False, {}, 8)
    assert text.index("rst = 1'b1;") < text.index("rst = 1'b0;")


def test_an_input_wider_than_the_driver_is_refused_not_truncated():
    ports = dict(PORTS, wide={"direction": "input", "width": 96, "signed": False,
                              "bits": list(range(96))})
    with pytest.raises(rsa.Refused) as caught:
        rsa.tb_source("d", ports, "clk", "rst_n", True, {}, 8)
    assert caught.value.reason == "port-too-wide"


def test_a_design_with_no_outputs_cannot_be_checked_for_x():
    ports = {k: v for k, v in PORTS.items() if v["direction"] == "input"}
    with pytest.raises(rsa.Refused):
        rsa.tb_source("d", ports, "clk", "rst_n", True, {}, 8)


# ------------------------------------------------------------- simulation logs

GOOD = ("PARAM IDX_BITS 5\nSAMPLES 512\nFIRSTX 6\nPORTX y 506\n"
        "PORTX y_valid 0\nDONE\n")


def test_a_good_log_is_read_back_per_port():
    parsed = rsa.parse_sim(GOOD, ["y", "y_valid"], {"IDX_BITS": 5})
    assert parsed["x_cycles"] == {"y": 506, "y_valid": 0}
    assert (parsed["samples"], parsed["first_x"]) == (512, 6)


def test_a_truncated_log_is_refused_rather_than_read_as_zero_x():
    with pytest.raises(rsa.Refused) as caught:
        rsa.parse_sim("SAMPLES 512\nFIRSTX -1\nPORTX y 0\n", ["y"], None)
    assert caught.value.reason == "no-receipt"


def test_a_log_missing_a_ports_count_is_refused():
    with pytest.raises(rsa.Refused) as caught:
        rsa.parse_sim("SAMPLES 4\nFIRSTX -1\nPORTX y 0\nDONE\n", ["y", "y_valid"], None)
    assert "y_valid" in caught.value.detail


def test_zero_sampled_cycles_is_refused():
    with pytest.raises(rsa.Refused):
        rsa.parse_sim("SAMPLES 0\nFIRSTX -1\nPORTX y 0\nDONE\n", ["y"], None)


def test_a_simulation_that_ran_the_wrong_parameters_is_refused():
    """The iverilog `-P` trap: a hierarchical path is silently ignored, so the
    bench happily simulates the CLEAN design while the log looks fine."""
    stale = GOOD.replace("PARAM IDX_BITS 5", "PARAM IDX_BITS 4")
    with pytest.raises(rsa.Refused) as caught:
        rsa.parse_sim(stale, ["y", "y_valid"], {"IDX_BITS": 5})
    assert caught.value.reason == "injection-not-active"


# ----------------------------------------------------------- the static check

def _netlist(bits):
    return {"modules": {"top": {
        "ports": {"i": {"direction": "input", "bits": [2]},
                  "o": {"direction": "output", "bits": bits}},
        "cells": {"c": {"port_directions": {"A": "input", "Y": "output"},
                        "connections": {"A": [2], "Y": [4]}}}}}}


def test_a_constant_x_output_bit_is_seen():
    assert rsa.constant_x_output_bits(_netlist([4, "x"]), "top") == {"o": [1]}


def test_an_undriven_output_bit_is_seen():
    assert rsa.constant_x_output_bits(_netlist([4, 99]), "top") == {"o": [1]}


def test_a_fully_driven_output_is_clean():
    assert rsa.constant_x_output_bits(_netlist([4, "0"]), "top") == {}


# --------------------------------------------------- the three-outcome contract

def test_a_property_that_moved_withholds_the_area():
    state, reason = rsa.verdict({"rtl-simulation": {"moved": True},
                                "netlist-simulation": {"moved": False}})
    assert (state, reason) == ("REFUSED", "outputs-are-x")


def test_no_property_moving_reports_an_area():
    assert rsa.verdict({"rtl-simulation": {"moved": False}})[0] == "PASS"


@pytest.mark.parametrize("state,reason,expect,code", [
    ("PASS", "outputs-are-defined", None, 0),
    ("REFUSED", "outputs-are-x", None, 2),
    ("PASS", "outputs-are-defined", "area", 0),
    ("REFUSED", "outputs-are-x", "area", 1),
    ("REFUSED", "apparatus", "area", 2),
    ("REFUSED", "outputs-are-x", "refused-x", 0),
    ("PASS", "outputs-are-defined", "refused-x", 1),
    # The one that matters: a missing tool must NEVER look like the control fired.
    ("REFUSED", "apparatus", "refused-x", 2),
    ("REFUSED", "injection-not-active", "refused-x", 2),
    ("REFUSED", "no-receipt", "refused-x", 2),
])
def test_the_three_outcomes_are_distinguished(state, reason, expect, code):
    assert rsa.exit_code(state, reason, expect) == code


def test_a_missing_tool_is_refused_at_the_point_of_use(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(rsa.Refused) as caught:
        rsa.require_tools("yosys")
    assert caught.value.reason == "apparatus"


# ---------------------------------------------------------------- the fixture

def test_the_injection_names_the_bug_it_reinstates():
    injection = rsa.INJECTIONS["TANH_INDEX_OOR"]
    assert "ladder_dp_t16" in injection.shipped_as
    assert "1,917" in injection.shipped_as
    assert injection.params == {"IDX_BITS": 5}


def test_the_fixture_and_its_rom_are_on_disk():
    fixture = rsa.FIXTURES["tanh_dp"]
    for rel in fixture.sources + fixture.data:
        assert (rsa.ROOT / rel).is_file(), rel


def test_the_clean_fixture_parameters_are_not_the_injected_ones():
    """A control whose clean case IS the mutant cannot discriminate."""
    clean = rsa.FIXTURES["tanh_dp"].params
    for name, injection in rsa.INJECTIONS.items():
        assert any(clean.get(k) != v for k, v in injection.params.items()), name
