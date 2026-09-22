#!/usr/bin/env python3
"""Assess the published ULX3S baseline against IcePi Zero; never programs it.

This is a physical compatibility screen, not correctness or sound evidence.
The optional DSP breakdown reuses a hash-verified *historical* synthesis result.
No synthesis, routing, source migration or current-engine claim is implied.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = "https://github.com/cheyao/icepi-zero/blob/e01faa2bd35dcb7269827f8420b845d46c78c869"
CAPACITY = {"MULT18X18D": 28, "TRELLIS_COMB": 24288}
PUBLICATION = ROOT / "fpga/reports/selected/linux-85f/publication.json"


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def assess_publication(record):
    if (record.get("bitstream_qualified") is not True
            or record.get("state") != "BUILT-TIMING-PASS"):
        raise ValueError("publication is not a qualified build for its original target")
    utilization = record.get("timing", {}).get("utilization", {})
    excess, used = {}, {}
    for name, available in CAPACITY.items():
        count = utilization.get(name, {}).get("used")
        if type(count) is not int or count < 0:
            raise ValueError(f"missing or malformed utilization: {name}")
        used[name] = count
        if count > available:
            excess[name] = count - available
    mismatches = []
    if record.get("device") != "25k":
        mismatches.append("device")
    if record.get("package") != "CABGA256":
        mismatches.append("package")
    constraints = [p for p in record.get("source_sha256", {}) if p.endswith(".lpf")]
    if record.get("board") != "icepi-zero" or not constraints or any(
            not p.startswith("fpga/boards/icepi-zero-") for p in constraints):
        mismatches.append("board_constraints")
    return {
        "state": "INCOMPATIBLE" if mismatches or excess else "REQUIRES_BOARD_VERIFICATION",
        "ready_to_program": False,
        "scope": "identity and two resource limits only; no IcePi build, timing, audio or playback qualification",
        "target": {"board": "icepi-zero", "device": "25k", "package": "CABGA256",
                   "oscillator_mhz": 50, "capacity": CAPACITY},
        "recorded_build": {"commit": record.get("source_commit"),
                           "configuration": record.get("configuration"),
                           "device": record.get("device"), "package": record.get("package"),
                           "used": used, "simulation_evidence": record.get("simulation_evidence")},
        "identity_mismatches": mismatches,
        "resource_excess": excess,
    }


def pin_plan(revision):
    if revision not in ("1.2", "1.3", "1.4"):
        raise ValueError("confirm a supported PCB revision: 1.2, 1.3 or 1.4")
    # GPIO indexes and physical header numbers are distinct. Taken from the
    # pinned upstream LPFs; SPI and I2S assignments here are our proposed use.
    signals = {
        "spi_sck": (11, 23, "G2", "input"),
        "spi_mosi": (10, 19, "L2", "input"),
        "spi_miso": (9, 21, "J1", "output"),
        "spi_cs_n": (8, 24, "H2", "input"),
        "i2s_bclk": (18, 12, "N4", "output"),
        "i2s_lrclk": (19, 35, "E4", "output"),
        "i2s_sdata": (21, 40, "F2", "output"),
    }
    return {
        "state": "WIRING_PLAN_ONLY", "revision": revision,
        "upstream_lpf": f"{UPSTREAM}/gateware/v{revision}/icepi-zero-v{revision.replace('.', '_')}.lpf",
        "clock": {"site": "M2" if revision == "1.2" else "M1", "mhz": 50},
        "pll_candidate": {"CLKI_DIV": 2, "CLKFB_DIV": 1, "CLKOP_DIV": 29,
                          "CLKOS_DIV": 59, "core_mhz": 725 / 59,
                          "sample_hz": 725e6 / 59 / 256,
                          "state": "calculated; wrapper not implemented or routed"},
        "signals": {name: dict(zip(("gpio", "header_pin", "site", "direction"), values))
                    for name, values in signals.items()},
        "uart": {"fpga_rx_site": "K16", "fpga_tx_site": "K15",
                 "device": "onboard FT231X", "spi_bridge_implemented": False},
        "io_voltage": 3.3,
        "dac": "external I2S receiver; module and power wiring not yet selected",
    }


def count_dsp(path, expected_sha256):
    data = Path(path).read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != expected_sha256:
        raise ValueError("synthesis netlist hash does not match its recorded build")
    try:
        cells = json.loads(data)["modules"]["ulx3s_top"]["cells"]
    except KeyError as exc:
        raise ValueError("historical netlist top ulx3s_top is missing") from exc
    counts, sources = Counter(), Counter()
    for name, cell in cells.items():
        if cell["type"] != "MULT18X18D":
            continue
        src = cell.get("attributes", {}).get("src", "")
        if "rate_conv_2x.v:" in src:
            block = "rate_converter"
            # Preserve exact rate-converter source spans, not long generated names.
            for span in set(s.split("/")[-1] for s in src.split("|") if "rate_conv_2x.v:" in s):
                sources[span] += 1
        elif name.startswith("u_synth.u_voice.osc2_path."):
            block = "oscillator_2x"
        elif name.startswith("u_synth.u_voice.u_ladder."):
            block = "ladder"
        elif name.startswith("u_synth.u_drums."):
            block = "drums"
        elif name.startswith("u_synth.u_voice."):
            block = "other_voice"
        else:
            block = "unattributed"
        counts[block] += 1
    return {"netlist_sha256": digest, "total": sum(counts.values()),
            "by_block": dict(sorted(counts.items())),
            "rate_converter_source_spans": dict(sorted(sources.items())),
            "scope": "cell count from historical synthesis; neither timing nor sound correctness"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", choices=("1.2", "1.3", "1.4"),
                        help="PCB silkscreen revision; do not infer from the product name")
    parser.add_argument("--synthesis-report", type=Path,
                        help="optional historical report whose synthesis artifact is available")
    parser.add_argument("--out", type=Path, default=ROOT / "build/icepi/preflight.json")
    args = parser.parse_args(argv)
    try:
        publication = json.loads(PUBLICATION.read_text())
        bitstream = PUBLICATION.with_name("ecp5.bit")
        if sha256(bitstream) != publication["original_artifact_sha256"]["ecp5.bit"]:
            raise ValueError("published bitstream hash differs")
        result = assess_publication(publication)
        result["publication_sha256"] = sha256(PUBLICATION)
        result["bitstream_sha256"] = sha256(bitstream)
        result["sources"] = [f"{UPSTREAM}/gateware/README.md",
                             f"{UPSTREAM}/gateware/Makefile",
                             "https://www.crowdsupply.com/icy-electronics/icepi-zero"]
        result["pcb_revision_confirmed"] = args.revision is not None
        result["pin_plan"] = pin_plan(args.revision) if args.revision else None
        if args.synthesis_report:
            prior = json.loads(args.synthesis_report.read_text())
            stage = prior["stages"]["synthesis"]
            if stage["state"] != "PASS" or stage["exit_code"] != 0:
                raise ValueError("saved synthesis did not pass")
            # The local 25F attempt and published 85F build share all HDL inputs.
            # Establish that before using the local netlist to explain the 85F count.
            prior_hdl = {p for p in prior["source_sha256"] if p.endswith(".v")}
            published_hdl = {p for p in publication["source_sha256"] if p.endswith(".v")}
            if prior_hdl != published_hdl or any(
                    publication["source_sha256"].get(p) != h
                    for p, h in prior["source_sha256"].items()):
                raise ValueError("saved synthesis source hashes differ from publication")
            if any(prior["configuration"].get(flag) != publication["configuration"][flag]
                   for flag in ("OSC2X", "FILTER2X")) or prior["configuration"].get("PULSE2X", 0) != 0:
                raise ValueError("saved synthesis configuration differs from baseline")
            dsp = count_dsp(stage["artifact"], stage["artifact_sha256"])
            if dsp["total"] != result["recorded_build"]["used"]["MULT18X18D"]:
                raise ValueError("synthesis DSP total differs from publication")
            result["historical_dsp"] = {**dsp, "source_commit": prior["source_commit"],
                                        "yosys_version": prior.get("yosys_version"),
                                        "synthesis_report_sha256": sha256(args.synthesis_report)}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        result = {"state": "REFUSED", "reason": str(exc), "ready_to_program": False}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(f"{result['state']}: {args.out}")
    if "reason" in result:
        print(result["reason"])
    return 2 if result["state"] == "REFUSED" else 1 if result["state"] == "INCOMPATIBLE" else 0


if __name__ == "__main__":
    raise SystemExit(main())
