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
    assert "set_output_delay" not in text
    pins = dict(re.findall(r"PACKAGE_PIN (\w+) \[get_ports (\w+)\]", text))
    assert pins == {"D4": "sd_left", "D3": "sd_right"}
    # JA (I2S) and JB (SPI) pins stay where the shared XDC puts them
    shared = dict(re.findall(r"PACKAGE_PIN (\w+) \[get_ports (\w+)\]", build.XDC.read_text()))
    assert not set(pins) & set(shared)


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
