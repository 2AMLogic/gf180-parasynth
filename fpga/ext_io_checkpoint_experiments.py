#!/usr/bin/env python3
"""Checkpoint constraint experiments for the seven external outputs.

Refuses unless the routed checkpoint matches the published SHA-256, then runs
constraint variants through one flock'd Vivado batch:

  C1 diagnostic -- committed budgets with i2s od_min -8.2 (captures the exact
                   hold violations that value produces)
  C2 candidate  -- i2s od_min 154.560 (structural half-period window minus the
                   DAC tDH budget); must be clean
  D candidate + nominal output delay on the i2s_bclk port itself

Earlier sessions proved the baseline (A: 7 missing output delays, WNS 46.498)
and the red case (B: impossible budgets, WNS -119.996, 3 failing endpoints).

Parses every report and prints a digest. Exit 0 = variants behaved as
designed; 2 = REFUSED (hash mismatch); 1 = a variant deviated.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DCP = Path("/home/ubuntu/parasynth-arty-attempt02/build/arty/routed.dcp")
EXPECT = "fdb3b45d7d7b108bf3246a5013f7ac6171f126956a4d0fcc2af1a20e499945fe"
VIVADO_ENV = "source /tools/Xilinx/2025.1/Vivado/settings64.sh"


def refuse(reason: str) -> int:
    print(f"REFUSED: {reason}")
    return 2


def run_vivado() -> int:
    command = (f"{VIVADO_ENV} && flock -w 7200 /home/ubuntu/vivado.lock "
               f"vivado -mode batch -source {HERE}/ext_io_checkpoint_experiments.tcl "
               f"-log {HERE}/exp.log -journal {HERE}/exp.jou")
    return subprocess.run(["bash", "-c", command], cwd=HERE).returncode


SUMMARY_KEYS = ("wns_ns", "tns_ns", "setup_failing", "setup_endpoints", "whs_ns",
                "ths_ns", "hold_failing", "hold_endpoints", "wpws_ns", "tpws_ns",
                "pulse_failing", "pulse_endpoints")


def parse_timing(rpt: Path) -> dict:
    text = rpt.read_text()
    section = text.split("| Design Timing Summary", 1)[1].split("| Clock Summary", 1)[0]
    rows = [s.split() for s in section.splitlines() if re.match(r"^\s*[-+]?\d+\.\d+\s", s)]
    out = dict(zip(SUMMARY_KEYS, map(float, rows[0])))
    for kind in ("no_clock", "unconstrained_internal_endpoints", "no_output_delay",
                 "no_input_delay", "generated_clocks"):
        counts = re.findall(r"checking " + kind + r" \((\d+)\)", text)
        out[kind] = sorted({int(n) for n in counts})
    ports = re.findall(r"ports with no output delay specified.*?\n+(.*?)\n\n",
                       text, re.DOTALL)
    out["no_output_delay_ports"] = ports[0].split() if ports else []
    return out


def parse_io(rpt: Path) -> list:
    text = rpt.read_text()
    if not text.strip():
        return []
    paths = []
    for chunk in text.split("Slack (")[1:]:
        def grab(pattern: str):
            m = re.search(pattern, chunk)
            return m.group(1).strip() if m else None
        paths.append({
            "slack_ns": float(grab(r"^(?:MET|VIOLATED)\)\s*:?\s*([-+]?[\d.]+)ns") or 0),
            "source": grab(r"Source:\s*(\S+)"),
            "destination": grab(r"Destination:\s*(\S+)"),
            "requirement_ns": grab(r"Requirement:\s*([-+]?[\d.]+)ns"),
            "data_delay_ns": grab(r"Data Path Delay:\s*([\d.]+)ns"),
        })
    return paths


def main() -> int:
    if not DCP.is_file():
        return refuse(f"routed checkpoint missing: {DCP}")
    digest = hashlib.sha256(DCP.read_bytes()).hexdigest()
    print(f"checkpoint sha256 {digest}")
    if digest != EXPECT:
        return refuse("checkpoint hash mismatch")
    rc = run_vivado()
    print(f"vivado exit {rc}")

    verdicts = {}
    for tag in ("C1", "C2", "D"):
        rpt = HERE / f"{tag}_timing.rpt"
        verdicts[tag] = parse_timing(rpt) if rpt.is_file() else {"missing": True}
    io = {}
    for tag in ("C1", "C2"):
        setup, hold = HERE / f"{tag}_io_setup.rpt", HERE / f"{tag}_io_hold.rpt"
        if setup.is_file():
            io[tag] = {"setup": parse_io(setup), "hold": parse_io(hold)}

    print(json.dumps({"timing": verdicts, "io": io}, indent=2))

    ok = rc == 0
    c1, c2, d = verdicts["C1"], verdicts["C2"], verdicts["D"]
    if c1.get("hold_failing", 0) and io.get("C1", {}).get("hold"):
        print(f"DIAGNOSTIC C1: {c1['hold_failing']:.0f} failing hold endpoints, "
              f"whs {c1['whs_ns']} ns; worst paths:")
        for p in sorted(io["C1"]["hold"], key=lambda p: p["slack_ns"])[:4]:
            print(f"    {p}")
    else:
        ok = False
        print("DIAGNOSTIC C1: expected failing hold endpoints, none captured")
    if c2.get("hold_failing", 1) or c2.get("setup_failing", 1) or c2.get("tpws_ns", -1):
        ok = False
        print("CANDIDATE C2: failing endpoints present")
    else:
        print(f"CANDIDATE C2 clean: wns {c2['wns_ns']} ns, whs {c2['whs_ns']} ns, "
              f"no_output_delay {c2['no_output_delay']} {c2['no_output_delay_ports']}")
    if d.get("hold_failing", 1) or d.get("setup_failing", 1):
        ok = False
        print("VARIANT D: failing endpoints present")
    else:
        print(f"D (bclk output delay added): no_output_delay {d.get('no_output_delay')}, "
              f"wns {d.get('wns_ns')} ns, whs {d.get('whs_ns')} ns")
    print("EXPERIMENTS " + ("BEHAVED AS DESIGNED" if ok else "DEVIATED -- see above"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
