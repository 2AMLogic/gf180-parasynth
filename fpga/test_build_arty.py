import hashlib
import json
from pathlib import Path

import pytest

import build_arty as build


def test_freezes_selected_baseline_and_full_core_sources():
    assert build.CONFIG == {"OSC2X": 1, "FILTER2X": 1, "PULSE2X": 0}
    paths = build.sources()
    assert paths[0].name == "arty_a7_top.v"
    assert {p.name for p in paths} >= {"drum_kit.v", "drum_dp.v", "modal_dp.v",
                                      "rate_conv_2x.v", "spi_ctl.v", "i2s_tx.v"}
    text = build.tcl_script(Path("/tmp/arty test"), paths)
    assert "xc7a100tcsg324-1" in text
    assert "VOICE_FILTER_2X" in text and "VOICE_OSC_2X" in text
    assert "VOICE_PULSE_2X" not in text
    assert "route_design" in text and "write_bitstream" in text
    assert "SIM_NO_MMCM=1" not in text
    assert "SIM_NO_MMCM=0" in text


def test_verification_cannot_be_missing_stale_or_mutated(tmp_path):
    source = tmp_path / "core.v"
    source.write_text("module core; endmodule")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    result = tmp_path / "verification.json"
    with pytest.raises(ValueError, match="verification"):
        build.validate_verification(result, [source])
    result.write_text(json.dumps({"state": "PASS", "exit_code": 0, "inject": None,
        "configuration": build.CONFIG, "source_sha256": {str(source): digest},
        "transcript_sha256": hashlib.sha256(b"numerical pass").hexdigest(),
        "comparison": {"wire_mismatch": 0, "swap": 0, "width": 0, "core_bad": 0,
            "writes_bad": 0, "frame_pred_bad": 0, "frame_no_pred": 0,
            "busy_at_tick": 0, "overrun": 0, "overflow": 0, "frames_no_sample": 0,
            "periods": 20, "writes_seen": 3, "writes_sent": 3, "worst_strobe_cycle": 175}}))
    result.with_name("verification.txt").write_text("numerical pass")
    build.validate_verification(result, [source])
    source.write_text("module wrong; endmodule")
    with pytest.raises(ValueError, match="source"):
        build.validate_verification(result, [source])
    source.write_text("module core; endmodule")
    record = json.loads(result.read_text())
    record["inject"] = "ARTY_SDATA_ZERO"
    result.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="clean"):
        build.validate_verification(result, [source])
    record["inject"] = None
    for field, value in [("periods", 0), ("writes_seen", 0), ("writes_sent", 4),
                         ("wire_mismatch", 1), ("worst_strobe_cycle", 256)]:
        changed = {**record, "comparison": {**record["comparison"], field: value}}
        result.write_text(json.dumps(changed))
        with pytest.raises(ValueError, match="numerical comparison"):
            build.validate_verification(result, [source])
    result.write_text(json.dumps(record))
    result.with_name("verification.txt").write_text("different run")
    with pytest.raises(ValueError, match="transcript"):
        build.validate_verification(result, [source])


def test_clock_parameters_have_exact_nominal_sample_rate():
    from fractions import Fraction
    # Independently calculate the MMCM clock from parameters in the RTL.
    import re
    text = (build.ROOT / "fpga/rtl/arty_a7_top.v").read_text()
    values = {name: Fraction(re.search(r"\." + name + r"\(([0-9.]+)\)", text)[1])
              for name in ("CLKFBOUT_MULT_F", "DIVCLK_DIVIDE", "CLKOUT0_DIVIDE_F")}
    vco = 100 * values["CLKFBOUT_MULT_F"] / values["DIVCLK_DIVIDE"]
    assert 600 <= vco <= 1200
    assert vco / values["CLKOUT0_DIVIDE_F"] * 1000000 / 256 == 48000


def test_control_rejects_refusal_and_unrelated_failure():
    from verify_arty_controls import caught
    assert not caught({"state": "REFUSED", "exit_code": 2}, "ARTY_MOSI_ZERO")
    assert not caught({"state": "FAIL", "exit_code": 1, "comparison": {}}, "ARTY_MOSI_ZERO")
    assert not caught({"state": "FAIL", "exit_code": 1, "comparison": {
        "periods": 100, "wire_mismatch": 100, "core_bad": 100}}, "ARTY_SDATA_ZERO")


def test_tcl_paths_cannot_execute_substitutions():
    import shutil
    import subprocess
    if not shutil.which("tclsh"):
        pytest.skip("Tcl interpreter unavailable")
    value = "/tmp/spaces [error bad] $env(HOME)"
    result = subprocess.run(["tclsh"], input="puts " + build.tcl_word(value),
                            text=True, capture_output=True, check=True)
    assert result.stdout.strip() == value
    with pytest.raises(ValueError):
        build.tcl_word("/tmp/}; error bad")


def test_real_reset_reasserts_and_waits_for_clock_lock(tmp_path):
    import shutil
    import subprocess
    if not shutil.which("iverilog"):
        pytest.skip("iverilog unavailable")
    bench = tmp_path / "reset.v"
    bench.write_text('''`timescale 1ns/1ps
module synth_top(input clk, rst_n_pad, sck, mosi, cs_n,
                 output miso, bclk, lrclk, sdata);
assign {miso,bclk,lrclk,sdata} = 4'b0000;
endmodule
module reset_test;
reg clk=0, button=1;
always #5 clk=~clk;
arty_a7_top #(.SIM_NO_MMCM(1), .POR_BITS(3)) dut (
 .clk_100mhz(clk), .btn_reset(button), .spi_sck(1'b0),
 .spi_mosi(1'b0), .spi_cs_n(1'b1));
initial begin
 #22; if (dut.core_rst_n !== 0) $fatal(1,"reset initially asserted");
 button=0;
 #10; if (dut.core_rst_n !== 0) $fatal(1,"released too early");
 #140; if (dut.core_rst_n !== 1) $fatal(1,"did not release");
 button=1;
 #1; if (dut.core_rst_n !== 0) $fatal(1,"button did not reset");
 button=0;
 force dut.clock_locked=0;
 #150; if (dut.core_rst_n !== 0) $fatal(1,"released without lock");
 release dut.clock_locked;
 #150; if (dut.core_rst_n !== 1) $fatal(1,"did not recover lock");
 $display("RESET PASS"); $finish;
end
endmodule
''')
    executable = tmp_path / "reset.vvp"
    subprocess.run(["iverilog", "-g2012", "-s", "reset_test", "-o", str(executable),
                    str(bench), str(build.ROOT / "fpga/rtl/arty_a7_top.v")], check=True)
    result = subprocess.run(["vvp", str(executable)], text=True, capture_output=True, check=True)
    assert "RESET PASS" in result.stdout
