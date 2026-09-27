#!/usr/bin/env python3
"""Prepare or run a checked Arty A7-100T Vivado build.

A bitstream alone does not qualify I/O timing or physical playback.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import time

import build_selected
import xdc_bindings as xb

ROOT = Path(__file__).resolve().parents[1]
CONFIG = {"OSC2X": 1, "FILTER2X": 1, "PULSE2X": 0}
# The images this builder can build, by configuration. R1 is the default and
# every existing path (R1's records, the publisher's R1 checks) uses it
# unchanged; the R2 candidate adds PULSE2X=1 (#333, fpga/release/R2.md).
IMAGE_CONFIGS = {"r1": CONFIG, "r2": {"OSC2X": 1, "FILTER2X": 1, "PULSE2X": 1}}
IMAGE_VERIFICATION = {"r1": ROOT / "fpga/reports/arty/rev14-clean/verification.json",
                      "r2": ROOT / "fpga/reports/arty/r2-clean/verification.json"}


def config_defines(config) -> list:
    return (["VOICE_OSC_2X"] if config["OSC2X"] else []) \
        + (["VOICE_FILTER_2X"] if config["FILTER2X"] else []) \
        + (["VOICE_PULSE_2X"] if config["PULSE2X"] else [])
PART = "xc7a100tcsg324-1"
XDC = ROOT / "fpga/boards/arty-a7-100.xdc"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sources():
    # uart_bridge.v: the USB-UART control bridge, instantiated by the wrapper
    # (synth_top WITH_UART=1) as a second front end for the register-write port.
    return [ROOT / "fpga/rtl/arty_a7_top.v"] + build_selected.sources()[1:] \
        + [ROOT / "rtl-sketch/uart_bridge.v"]


def roms():
    """Resolve ROM defaults relative to the RTL working directory."""
    paths = set((ROOT / "rtl-sketch").glob("*.hex"))
    for source in sources():
        for name in re.findall(r'parameter \w+\s*=\s*"([^"]+\.hex)"', source.read_text()):
            path = (ROOT / "rtl-sketch" / name).resolve()
            if not path.is_relative_to(ROOT):
                raise ValueError("ROM path leaves the repository: " + name)
            paths.add(path)
    return sorted(paths)


def tcl_word(value):
    # Braced Tcl words do not perform command/variable substitution.
    value = str(value)
    if any(c in value for c in "{}\\\n\r"):
        raise ValueError("unsupported character in Tcl path")
    return "{" + value + "}"


def tcl_script(directory, paths, constraints=XDC, config=CONFIG):
    return "\n".join([
        "set_param general.maxThreads 4",
        "read_verilog [list "
        + " ".join(tcl_word(p) for p in paths) + "]",
        "read_xdc " + tcl_word(constraints),
        "synth_design -top arty_a7_top -part " + PART
        + " -generic {SIM_NO_MMCM=0 POR_BITS=12} -flatten_hierarchy none"
        + "".join(" -verilog_define " + d for d in config_defines(config)),
        "write_checkpoint -force " + tcl_word(directory / "synthesized.dcp"),
        # #315: every XDC object query must bind exactly its objects, or the
        # build stops here (exit 3) instead of shipping a dropped constraint
        xb.tcl_assertions(Path(constraints).read_text(),
                          str(directory / xb.REPORT)),
        "opt_design", "place_design", "phys_opt_design", "route_design",
        # #315 / plan099: on the ROUTED design, the UART synchroniser constraints
        # hit the intended flops and arc, and nothing else is excepted
        xb.tcl_route_checks(str(directory / xb.REPORT), str(directory / xb.EXCEPTIONS)),
        "report_utilization -file " + tcl_word(directory / "utilization.rpt"),
        "report_timing_summary -check_timing_verbose -file " + tcl_word(directory / "timing.rpt"),
        "report_clocks -file " + tcl_word(directory / "clocks.rpt"),
        "report_drc -file " + tcl_word(directory / "drc.rpt"),
        "write_checkpoint -force " + tcl_word(directory / "routed.dcp"),
        "write_bitstream -force " + tcl_word(directory / "arty.bit"), "exit", ""])


def validate_verification(path, paths, config=CONFIG):
    try:
        record = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise ValueError("missing or unreadable Arty verification") from exc
    if (record.get("state") != "PASS" or record.get("exit_code") != 0
            or record.get("inject") is not None or record.get("configuration") != config):
        raise ValueError("verification must be a clean pass of the selected configuration")
    comparison = record.get("comparison", {})
    zero_fields = ("wire_mismatch", "swap", "width", "core_bad", "writes_bad",
                   "frame_pred_bad", "frame_no_pred", "busy_at_tick", "overrun",
                   "overflow", "frames_no_sample")
    if (any(comparison.get(k) != 0 for k in zero_fields)
            or comparison.get("periods", 0) < 1 or comparison.get("writes_seen", 0) < 1
            or comparison.get("writes_seen") != comparison.get("writes_sent")
            or not 0 < comparison.get("worst_strobe_cycle", 256) < 256):
        raise ValueError("verification has no complete, passing numerical comparison")
    for source in paths:
        key = str(source.relative_to(ROOT)) if source.is_relative_to(ROOT) else str(source)
        if record.get("source_sha256", {}).get(key) != sha(source):
            raise ValueError("verification source differs: " + key)
    transcript = path.with_name("verification.txt")
    if not transcript.is_file() or sha(transcript) != record.get("transcript_sha256"):
        raise ValueError("verification transcript is absent or changed")
    return {"record_sha256": sha(path), "transcript_sha256": sha(transcript),
            "periods": comparison["periods"], "scope": record.get("scope")}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "build/arty")
    parser.add_argument("--verification", type=Path,
                        default=ROOT / "build/arty-controls/clean/verification.json")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--vivado", default="vivado")
    parser.add_argument("--image", choices=sorted(IMAGE_CONFIGS), default="r1",
                        help="which image's configuration to build (default r1)")
    args = parser.parse_args(argv)
    config = IMAGE_CONFIGS[args.image]
    directory = args.out.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    files = sources() + roms()
    report = {"state": "REFUSED", "part": PART, "configuration": config, "image": args.image,
              "hardware_playback_tested": False, "external_io_timing_qualified": False,
              "source_sha256": {str(p.relative_to(ROOT)): sha(p) for p in files + [XDC]}}
    report_path = directory / "report.json"

    def save():
        report_path.write_text(json.dumps(report, indent=2) + "\n")
        print(report["state"], report.get("reason", report_path), flush=True)

    try:
        report["verification"] = validate_verification(args.verification, files, config)
        snapshots = []
        for path in sources() + [XDC]:
            dest = directory / "inputs" / path.relative_to(ROOT)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, dest)
            snapshots.append(dest)
        for path in roms():
            dest = directory / "inputs" / path.relative_to(ROOT)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, dest)
            if sha(dest) != report["source_sha256"][str(path.relative_to(ROOT))]:
                raise ValueError("ROM changed during snapshot")
        for original, copied in zip(sources() + [XDC], snapshots):
            if sha(copied) != report["source_sha256"][str(original.relative_to(ROOT))]:
                raise ValueError("source changed during snapshot: " + str(original))
        script = directory / "build.tcl"
        script.write_text(tcl_script(directory, snapshots[:-1], snapshots[-1], config))
        report["script_sha256"] = sha(script)
        if args.prepare_only:
            report["state"] = "PREPARED"
            save()
            return 0
        executable = shutil.which(args.vivado)
        if executable is None:
            raise ValueError("Vivado executable unavailable; no fit/timing/bitstream result")
        report["vivado_version"] = subprocess.check_output([executable, "-version"], text=True)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        report["reason"] = str(exc)
        save()
        return 2
    outputs = [directory / name for name in
               ("arty.bit", "utilization.rpt", "timing.rpt", "clocks.rpt", "drc.rpt", "routed.dcp",
                xb.REPORT, xb.EXCEPTIONS)]
    for path in outputs:
        path.unlink(missing_ok=True)
    started = time.monotonic()
    command = [executable, "-mode", "batch", "-source", str(script),
               "-log", str(directory / "vivado.log"), "-journal", str(directory / "vivado.jou")]
    report["command"] = command
    with (directory / "runner.log").open("w") as log:
        process = subprocess.Popen(command, cwd=directory / "inputs/rtl-sketch",
                                   stdout=log, stderr=subprocess.STDOUT,
                                   start_new_session=True)
        try:
            rc = process.wait(timeout=7200)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            report.update(reason="Vivado exceeded 2 hours", state="REFUSED")
            save()
            return 2
    report.update(exit_code=rc, seconds=round(time.monotonic() - started, 3))
    complete = rc == 0 and all(p.is_file() and p.stat().st_size > 0 for p in outputs)
    report["state"] = "BUILT_REQUIRES_TIMING_REVIEW" if complete else "FAIL"
    report["artifact_sha256"] = {p.name: sha(p) for p in outputs if p.is_file()}
    save()
    return 0 if complete else 1


if __name__ == "__main__":
    raise SystemExit(main())
