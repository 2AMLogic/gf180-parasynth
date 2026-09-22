#!/usr/bin/env python3
"""Arty wrapper SPI -> I2S compared with the existing integer model.

SIM_NO_MMCM supplies the core clock directly. This exercises the real wrapper's
reset and signal connections, but excludes the analog MMCM and physical pins.
The canonical bench still decodes both I2S channels and predicts SPI delivery.
"""
from __future__ import annotations
import argparse
import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "rtl-sketch"))
import verify_synth_top as top
from build_arty import roms


def adapted_bench():
    text = (ROOT / "rtl-sketch/tb_top_bx.v").read_text()
    replacements = {
        "reg clk = 0, rst_n = 0;": "reg clk = 0, btn_reset = 1;\n    wire rst_n = board.core_rst_n;",
        "synth_top dut (.clk(clk), .rst_n_pad(rst_n), .sck(sck), .mosi(mosi), .cs_n(cs_n), .miso(miso),\n                   .bclk(bclk), .lrclk(lrclk), .sdata(sdata));":
            "arty_a7_top #(.SIM_NO_MMCM(1), .POR_BITS(3)) board (\n"
            "        .clk_100mhz(clk), .btn_reset(btn_reset), .led(),\n"
            "        .spi_sck(sck), .spi_mosi(mosi), .spi_cs_n(cs_n), .spi_miso(miso),\n"
            "        .i2s_bclk(bclk), .i2s_lrclk(lrclk), .i2s_sdata(sdata));",
        "@(negedge clk) rst_n = 1;":
            "@(negedge clk) btn_reset = 0; wait (rst_n === 1'b1); @(negedge clk);",
    }
    for before, after in replacements.items():
        if text.count(before) != 1:
            raise ValueError("canonical bench changed: Arty adapter must be reviewed")
        text = text.replace(before, after)
    text = text.replace("dut.", "board.u_synth.")
    # Bound startup. A stuck reset is an actual failing wrapper, not a hung run.
    text = text.replace("wire rst_n = board.core_rst_n;", "wire rst_n = board.core_rst_n;\n"
        "    initial begin #10000; if (rst_n !== 1'b1) $fatal(1, \"Arty reset did not release\"); end")
    return text


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outdir", type=Path, default=ROOT / "build/arty-verify")
    parser.add_argument("--rtl", type=Path, default=ROOT / "fpga/rtl/arty_a7_top.v")
    parser.add_argument("--inject", choices=("ARTY_MOSI_ZERO", "ARTY_SDATA_ZERO"))
    args = parser.parse_args(argv)
    directory = args.outdir.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    bench = directory / "tb_top_bx.v"
    bench.write_text(adapted_bench())
    original = top.resolve_sources

    def sources(rtl_dir):
        resolved = [(n, str(bench) if n == "tb_top_bx.v" else p)
                    for n, p in original(rtl_dir)]
        return resolved + [("arty_a7_top.v", str(args.rtl.resolve()))]

    top.resolve_sources = sources
    command = ["--m5a-smoke", "--filter2x", "--m5a-saw-cutoff-hz", "20000",
               "--m5a-saw-volume-correction-db", "-0.45428", "--outdir", str(directory)]
    if args.inject:
        command += ["--inject", args.inject]
    transcript = io.StringIO()
    try:
        with contextlib.redirect_stdout(transcript):
            rc = top.main(command)
    finally:
        top.resolve_sources = original
    content = transcript.getvalue()
    (directory / "verification.txt").write_text(content)
    print(content, end="")
    hashed = {str(Path(p).relative_to(ROOT)) if Path(p).is_relative_to(ROOT) else str(p):
              hashlib.sha256(Path(p).read_bytes()).hexdigest() for _, p in sources(None)}
    for path in roms():
        hashed[str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
    record = {"state": "PASS" if rc == 0 else "FAIL" if rc == 1 else "REFUSED",
              "exit_code": rc, "configuration": {"OSC2X": 1, "FILTER2X": 1, "PULSE2X": 0},
              "scope": "Arty wrapper digital SPI-to-I2S smoke vs Python; MMCM bypassed; no physical timing claim",
              "inject": args.inject, "source_sha256": hashed,
              "transcript_sha256": hashlib.sha256(content.encode()).hexdigest(),
              "comparison": top.LAST}
    (directory / "verification.json").write_text(json.dumps(record, indent=2) + "\n")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
