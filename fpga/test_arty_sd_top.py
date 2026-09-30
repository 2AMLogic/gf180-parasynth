"""The no-DAC demo wrapper (#406): structure, reset, wiring, and its build gate.

The modulator's own numbers (in-band SNR, its controls) are fpga/verify_sd_dac.py,
run by `make verify`; this file covers what that bench does not: the wrapper."""
import re
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

import build_arty as build
import build_arty_sd as sd
import publish_arty as publish
import verify_sd_dac as vsd

ROOT = build.ROOT
RTL = ROOT / "fpga/rtl"


def _mmcm(text):
    block = re.search(r"MMCME2_BASE #\((.*?)\) mmcm", text, re.DOTALL)
    assert block, "no MMCM"
    return re.sub(r"\s+", "", block[1])


def test_mmcm_matches_arty_a7_top_parameter_for_parameter():
    # the shared XDC names hardware_clock.mmcm/CLKOUT0 and the derived clock
    # hardware_clock.clock_raw; the SD wrapper owns an identical MMCM there
    top = (RTL / "arty_a7_top.v").read_text()
    sdt = (RTL / "arty_a7_sd_top.v").read_text()
    assert _mmcm(top) == _mmcm(sdt)
    assert "end else begin: hardware_clock" in sdt
    assert re.search(r"\.CLKOUT0\(clock_raw\)", sdt)


def test_never_published_as_a_release_image():
    assert sd.TOP not in publish.VERIFICATION_BY_WRAPPER


def test_prepared_script_reads_both_constraint_files_in_order(tmp_path):
    paths = [tmp_path / "a.v"]
    text = build.tcl_script(tmp_path, paths, build.XDC, top=sd.TOP,
                            extra_constraints=[sd.SD_XDC])
    xdcs = re.findall(r"^read_xdc \{([^}]+)\}", text, re.M)
    assert xdcs == [str(build.XDC), str(sd.SD_XDC)]
    assert "synth_design -top arty_a7_sd_top " in text
    assert "SIM_NO_MMCM=0" in text


def test_sd_xdc_dispositions_exactly_the_pdm_ports():
    import xdc_bindings as xb
    text = sd.SD_XDC.read_text()
    assert xb.expected_exceptions(text) == [("*", "[get_ports sd_left]"),
                                            ("*", "[get_ports sd_right]")]
    # the constant dac_sck carries no exception: Vivado would never apply it
    assert sd.CONSTANT_PORTS == ["dac_sck"]
    assert "set_output_delay" not in text


def test_effective_pins_are_the_direct_plug_layout():
    # the shared XDC, then the demo XDC's overrides, in the order Vivado reads them
    pins = sd.effective_pins([build.XDC.read_text(), sd.SD_XDC.read_text()])
    for port, site in sd.DIRECT_PLUG_PINS.items():
        assert pins[port] == site, port
    assert len(set(pins.values())) == len(pins)           # one port per site
    # the published images keep the jumper layout: the shared XDC alone
    shared = sd.effective_pins([build.XDC.read_text()])
    assert (shared["i2s_bclk"], shared["i2s_lrclk"], shared["i2s_sdata"]) == ("G13", "B11", "A11")


def test_direct_plug_table_is_the_breakout_header_order():
    # PCM5102 breakout header from its VIN end, into JA6..JA1 (Digilent: JA1..JA4
    # = G13 B11 A11 D12); written out independently of the XDC under test
    header = ["VIN", "GND", "LCK", "DIN", "BCK", "SCK"]
    ja = {6: "VCC", 5: "GND", 4: "D12", 3: "A11", 2: "B11", 1: "G13"}
    plug = dict(zip(header, [ja[n] for n in (6, 5, 4, 3, 2, 1)]))
    port_for = {"SCK": "dac_sck", "BCK": "i2s_bclk", "DIN": "i2s_sdata", "LCK": "i2s_lrclk"}
    assert {port_for[k]: plug[k] for k in port_for} == \
        {k: v for k, v in sd.DIRECT_PLUG_PINS.items() if k in port_for.values()}


def test_effective_pins_refuses_a_dict_form_package_pin_line():
    # #471: Vivado's -dict form (`set_property -dict { PACKAGE_PIN ... }`) is
    # not one of the forms the single-line regex parses. Silently dropping
    # the assignment would leave an incomplete pin map with no error; this
    # must REFUSE instead (docs/verification-rules.md: loud over silent).
    dict_form = ("set_property -dict { PACKAGE_PIN E3 IOSTANDARD LVCMOS33 } "
                "[get_ports clk_100mhz]\n")
    with pytest.raises(ValueError, match="unparseable PACKAGE_PIN"):
        sd.effective_pins([dict_form])


def test_effective_pins_refuses_any_unparseable_package_pin_line():
    # a made-up malformed form, distinct from -dict, to confirm the refusal
    # is general (any set_property line naming PACKAGE_PIN it cannot parse)
    # rather than special-cased to -dict specifically
    malformed = "set_property PACKAGE_PIN[E3] [get_ports clk_100mhz]\n"
    with pytest.raises(ValueError, match="unparseable PACKAGE_PIN"):
        sd.effective_pins([malformed])


def test_effective_pins_ignores_non_package_pin_set_property_lines():
    # lines that set other properties (IOSTANDARD, PULLUP, ...) are not
    # PACKAGE_PIN assignments and must not trip the new refusal
    text = ("set_property PACKAGE_PIN D9 [get_ports btn_reset]\n"
           "set_property IOSTANDARD LVCMOS33 [get_ports btn_reset]\n"
           "set_property CONFIG_VOLTAGE 3.3 [current_design]\n")
    assert sd.effective_pins([text]) == {"btn_reset": "D9"}


def test_no_xdc_this_project_reads_uses_the_dict_form():
    # scope check from #471: confirm this is hardening, not an active bug --
    # if any XDC this build reads ever grows a -dict PACKAGE_PIN line, this
    # test (not just effective_pins) goes red first
    for path in (build.XDC, sd.SD_XDC):
        assert "-dict" not in path.read_text(), path


def test_an_override_onto_an_occupied_site_is_refused():
    bad = sd.SD_XDC.read_text().replace(
        "set_property PACKAGE_PIN D12 [get_ports i2s_lrclk]\n"
        "set_property PACKAGE_PIN B11 [get_ports i2s_bclk]\n",
        "set_property PACKAGE_PIN B11 [get_ports i2s_bclk]\n"
        "set_property PACKAGE_PIN D12 [get_ports i2s_lrclk]\n")
    assert bad != sd.SD_XDC.read_text()
    with pytest.raises(ValueError, match="holds it"):
        sd.effective_pins([build.XDC.read_text(), bad])


def test_placed_pins_reads_a_report_io_table():
    rpt = ("| Pin Number | Signal Name | Bank Type  |\n"
           "| B11        | i2s_bclk    | High Range |\n"
           "| G13        | dac_sck     | High Range |\n")
    assert sd.placed_pins(rpt, ["i2s_bclk", "dac_sck", "sd_left"]) == \
        {"i2s_bclk": "B11", "dac_sck": "G13"}


def test_output_disposition_reads_a_real_published_report():
    timing = (ROOT / "fpga/reports/arty/r1-player-preview-2025.1/timing.rpt").read_text()
    assert sd.output_disposition(timing) == {
        "unconstrained": [], "false_path": [], "forwarded_clock": ["i2s_bclk"]}


def test_output_disposition_refuses_a_report_that_does_not_list_the_ports():
    timing = (ROOT / "fpga/reports/arty/r1-player-preview-2025.1/timing.rpt").read_text()
    lying = timing.replace("There are 0 ports with no output delay but user has a false path",
                           "There are 2 ports with no output delay but user has a false path")
    with pytest.raises(ValueError, match="not listed"):
        sd.output_disposition(lying)


def test_estimator_known_answers_hold():
    t = np.arange(512)
    samples = np.round(16000 * np.sin(2 * np.pi * t * 11 / 512)).astype(np.int64)
    got = vsd.check_estimator(samples)
    assert abs(got["known_inband_db"] - 70.0) <= 0.5
    assert got["known_oob_db"] >= 110


# ---- simulation: reset contract and wiring, with a stub core -----------------
STUB = '''`timescale 1ns/1ps
module synth_top #(parameter WITH_UART = 0, parameter UART_BAUD = 115200,
                   parameter UART_EVQ_DEPTH = 64, parameter UART_WRQ_DEPTH = 8)
                  (input clk, rst_n_pad, sck, mosi, cs_n, uart_rxd,
                   output miso, uart_txd, bclk, lrclk, sdata);
// a core that emits one constant sample through the chip's own transmitter
reg [7:0] cyc = 0;
always @(posedge clk) cyc <= rst_n_pad ? cyc + 8'd1 : 8'd0;
assign {miso, uart_txd} = 2'b01;
i2s_tx u_i2s (.clk(clk), .rst_n(rst_n_pad), .cyc(cyc), .sample_valid(cyc == 8'd176),
              .sample(16'sh4000), .bclk(bclk), .lrclk(lrclk), .sdata(sdata));
endmodule
'''


def _simulate(tmp_path, body):
    if not shutil.which("iverilog"):
        pytest.skip("iverilog unavailable")
    bench = tmp_path / "bench.v"
    bench.write_text(STUB + body)
    exe = tmp_path / "bench.vvp"
    subprocess.run(["iverilog", "-g2012", "-s", "bench", "-o", str(exe), str(bench),
                    str(RTL / "arty_a7_sd_top.v"), str(RTL / "arty_a7_top.v"),
                    str(RTL / "i2s_rx.v"), str(RTL / "sd_dac.v"),
                    str(ROOT / "rtl-sketch/i2s_tx.v")], check=True)
    return subprocess.run(["vvp", "-n", str(exe)], text=True, capture_output=True, check=True).stdout


def test_reset_waits_for_lock_through_the_inner_wrapper(tmp_path):
    out = _simulate(tmp_path, '''
module bench;
reg clk=0, button=1; always #5 clk=~clk;
arty_a7_sd_top #(.SIM_NO_MMCM(1), .POR_BITS(3)) dut (.clk_100mhz(clk), .btn_reset(button),
  .spi_sck(1'b0), .spi_mosi(1'b0), .spi_cs_n(1'b1), .uart_rxd(1'b1));
initial begin
 #22; if (dut.u_arty.core_rst_n !== 0) $fatal(1,"reset initially asserted");
 button=0;
 #140; if (dut.u_arty.core_rst_n !== 1) $fatal(1,"did not release");
 if (dut.sd_rst_n !== 1) $fatal(1,"demo path did not release");
 force dut.clock_locked=0;
 #1; if (dut.u_arty.core_rst_n !== 0 || dut.sd_rst_n !== 0) $fatal(1,"lock loss did not reset");
 #150; if (dut.u_arty.core_rst_n !== 0) $fatal(1,"released without lock");
 release dut.clock_locked;
 #150; if (dut.u_arty.core_rst_n !== 1) $fatal(1,"did not recover lock");
 $display("RESET PASS"); $finish;
end
endmodule
''')
    assert "RESET PASS" in out


def test_jd_pins_carry_the_modulated_i2s_pins(tmp_path):
    # the stub core sends 0x4000 (+0.5 FS); the decoded word must be it, both
    # pads must carry one stream, and its ones-density must be the modulator's
    # (1 + 7/8 * 0.5) / 2 = 0.71875
    out = _simulate(tmp_path, '''
module bench;
reg clk=0, button=1; always #5 clk=~clk;
wire l, r;
arty_a7_sd_top #(.SIM_NO_MMCM(1), .POR_BITS(3)) dut (.clk_100mhz(clk), .btn_reset(button),
  .spi_sck(1'b0), .spi_mosi(1'b0), .spi_cs_n(1'b1), .uart_rxd(1'b1), .sd_left(l), .sd_right(r));
integer i, ones = 0, differ = 0;
initial begin
 #22 button=0;
 repeat (256*6) @(posedge clk);
 if (dut.sd_sample !== 16'sh4000) $fatal(1,"decoded %h, sent 4000", dut.sd_sample);
 if (dut.dac_sck !== 1'b0) $fatal(1,"dac_sck is %b, must be held low", dut.dac_sck);
 for (i = 0; i < 256*64; i = i + 1) begin
   @(posedge clk); ones = ones + l; differ = differ + (l !== r);
 end
 $display("ONES %0d OF %0d DIFFER %0d", ones, 256*64, differ); $finish;
end
endmodule
''')
    m = re.search(r"ONES (\d+) OF (\d+) DIFFER (\d+)", out)
    assert m, out
    ones, total, differ = map(int, m.groups())
    assert differ == 0
    assert abs(ones / total - 0.71875) < 1e-3


def test_constant_port_evidence_matches_vivados_own_warning():
    # this calls sd.constant_port_evidence -- the SAME function check_implementation
    # (the build gate) calls -- so a change to the gate's pattern is exercised
    # here too. A regex re-written inside this test would go stale silently;
    # see the control below for what that failure mode looks like.
    #
    # the line Vivado 2025.1 printed for this port on the first direct-plug build
    line = "WARNING: [Synth 8-3917] design arty_a7_sd_top has port dac_sck driven by constant 0"
    assert sd.constant_port_evidence(line, "dac_sck")
    # the same line, naming a port that was never asked about
    assert not sd.constant_port_evidence(line, "sd_left")
    # a different message ID entirely must not be mistaken for this evidence
    other_id = line.replace("Synth 8-3917", "Synth 8-7080")
    assert not sd.constant_port_evidence(other_id, "dac_sck")


def test_constant_port_evidence_control_catches_a_broken_gate_pattern(monkeypatch):
    # injected-bug control (docs/verification-rules.md: start red, carry
    # injected-bug controls). Break the gate's pattern the way a plausible
    # bad edit would -- requiring "constant 1" instead of "constant 0" -- and
    # confirm that the assertion the test above makes on the real Vivado
    # warning line now goes red.
    #
    # Before this issue, test_constant_port_evidence_matches_vivados_own_warning
    # asserted a regex written independently inside the test, so this exact
    # break in build_arty_sd.check_implementation's pattern would NOT have
    # been caught: the test's own copy would keep matching regardless of what
    # the gate's copy did. Now both call constant_port_evidence, so breaking
    # it here breaks the real evidence check too.
    def broken_evidence(log_text: str, port: str) -> bool:
        return re.search(r"Synth 8-3917\].* port " + re.escape(port) + r" driven by constant 1",
                         log_text) is not None
    monkeypatch.setattr(sd, "constant_port_evidence", broken_evidence)
    real_warning = "WARNING: [Synth 8-3917] design arty_a7_sd_top has port dac_sck driven by constant 0"
    with pytest.raises(AssertionError):
        assert sd.constant_port_evidence(real_warning, "dac_sck")
