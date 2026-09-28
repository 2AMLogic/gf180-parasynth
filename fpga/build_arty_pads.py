#!/usr/bin/env python3
"""Prepare or run the Vivado build of the Arty PADS DEMO image (#449).

    python3 fpga/build_arty_pads.py --prepare-only     # gates + build.tcl, no Vivado
    python3 fpga/build_arty_pads.py                    # the full build (the build box)

A separate script from fpga/build_arty.py, which stays the release image's
build: a different top (arty_a7_pads_top), a different XDC
(fpga/boards/arty-a7-100-pads.xdc), and a different evidence gate. The pads
image is never a release image; its report lands in build/arty-pads (publish
a copy to fpga/reports/arty/pads-demo-2025.1 by hand once it passes).

REFUSES (exit 2) before Vivado starts unless:
  * the boot ROM re-derives R1's kit: fpga/pads_rom.check_rom on the ROM file
    that will be compiled -- the build-time half of the kit-hash gate (the
    bench runs the same gate before it simulates);
  * every clean pads-bench scenario (fpga/verify_pads_top.py: phases, switch,
    reset) is a PASS of THESE sources, byte for byte, with its transcript.

After route, Tcl in the build itself exits 3 unless:
  * every XDC object query bound exactly its objects (fpga/xdc_bindings, #315)
    and the UART RX synchroniser checks hold on the routed design;
  * each human input (btn[0..3], sw_pads, sw_reset) reaches exactly one flop,
    that flop feeds exactly one flop, both carry ASYNC_REG, and every timing
    path from the port is a False Path (pads_inputs.rpt).

The verdict is read from timing.rpt: PASS only with WNS >= 0, WHS >= 0 and
zero failing setup, hold and pulse-width endpoints. Anything else is FAIL.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in ("fpga", "model", "rtl-sketch"):
    if str(ROOT / _p) not in sys.path:
        sys.path.insert(0, str(ROOT / _p))

import build_arty                                   # noqa: E402
import pads_rom as pr                               # noqa: E402
import xdc_bindings as xb                           # noqa: E402

CONFIG = build_arty.CONFIG
PART = build_arty.PART
TOP = "arty_a7_pads_top"
XDC = ROOT / "fpga/boards/arty-a7-100-pads.xdc"
PADS_REPORT = "pads_inputs.rpt"
INPUTS = ("btn[0]", "btn[1]", "btn[2]", "btn[3]", "sw_pads", "sw_reset")
SCENARIOS = ("phases", "switch", "reset")
sha = build_arty.sha


def sources():
    """The wrapper, the pads front end and its generated ROM, then the release
    image's core list minus its wrapper (build_arty.sources()[0])."""
    return ([ROOT / "fpga/rtl/arty_a7_pads_top.v", ROOT / "fpga/rtl/pads_seq.v", pr.ROM_V]
            + build_arty.sources()[1:])


def roms():
    return build_arty.roms()


def tcl_pads_inputs(report_path: str) -> str:
    """Tcl run after route_design: the disposition of the six human inputs,
    checked on the routed netlist rather than trusted from the XDC text."""
    ports = " ".join("{" + p + "}" for p in INPUTS)
    return "\n".join([
        f"set pi_fh [open {{{report_path}}} w]", "set pi_bad 0",
        f"foreach pi_port [list {ports}] {{",
        "  set pi_p [get_ports $pi_port]",
        "  set pi_s1 [all_fanout -from $pi_p -flat -endpoints_only -only_cells]",
        "  set pi_s2 {}",
        "  if {[llength $pi_s1] == 1} { set pi_s2 [all_fanout -from [get_pins -of_objects "
        "$pi_s1 -filter {REF_PIN_NAME == Q}] -flat -endpoints_only -only_cells] }",
        "  set pi_ok [expr {[llength $pi_p] == 1 && [llength $pi_s1] == 1 && [llength $pi_s2] == 1}]",
        "  if {$pi_ok} { set pi_ok [expr {[get_property ASYNC_REG $pi_s1] == 1 && "
        "[get_property ASYNC_REG $pi_s2] == 1}] }",
        "  set pi_paths [get_timing_paths -from $pi_p -max_paths 10 -nworst 10]",
        "  if {[llength $pi_paths] < 1} { set pi_ok 0 }",
        "  foreach x $pi_paths { if {[get_property EXCEPTION $x] ne {False Path}} { set pi_ok 0 } }",
        '  puts $pi_fh "INPUT\\t$pi_port\\t$pi_ok\\tstage1 $pi_s1; stage2 $pi_s2; '
        '[llength $pi_paths] path(s), all False Path required"',
        '  if {!$pi_ok} { incr pi_bad; puts "PADS_INPUT_REFUSED $pi_port" }',
        "}",
        'puts $pi_fh "END\\t$pi_bad"', "close $pi_fh",
        'if {$pi_bad} { puts "PADS_INPUT_REFUSED: $pi_bad input(s)"; exit 3 }',
    ])


def tcl_script(directory, paths, constraints=XDC):
    tw = build_arty.tcl_word
    return "\n".join([
        "set_param general.maxThreads 4",
        "read_verilog [list " + " ".join(tw(p) for p in paths) + "]",
        "read_xdc " + tw(constraints),
        "synth_design -top " + TOP + " -part " + PART
        + " -generic {SIM_NO_MMCM=0 POR_BITS=12} -flatten_hierarchy none"
        + " -verilog_define VOICE_OSC_2X -verilog_define VOICE_FILTER_2X",
        "write_checkpoint -force " + tw(directory / "synthesized.dcp"),
        xb.tcl_assertions(Path(constraints).read_text(), str(directory / xb.REPORT)),
        "opt_design", "place_design", "phys_opt_design", "route_design",
        xb.tcl_route_checks(str(directory / xb.REPORT), str(directory / xb.EXCEPTIONS)),
        tcl_pads_inputs(str(directory / PADS_REPORT)),
        "report_utilization -file " + tw(directory / "utilization.rpt"),
        "report_timing_summary -check_timing_verbose -file " + tw(directory / "timing.rpt"),
        "report_clocks -file " + tw(directory / "clocks.rpt"),
        "report_drc -file " + tw(directory / "drc.rpt"),
        "write_checkpoint -force " + tw(directory / "routed.dcp"),
        "write_bitstream -force " + tw(directory / "arty_pads.bit"), "exit", ""])


# ---- the gates before Vivado ------------------------------------------------------
def validate_verification(directory: Path, paths: list) -> dict:
    """Every clean scenario must be a PASS of exactly these sources."""
    out = {}
    for sc in SCENARIOS:
        path = Path(directory) / sc / "verification.json"
        try:
            rec = json.loads(path.read_text())
        except (OSError, ValueError) as exc:
            raise ValueError(f"pads verification {sc}: missing or unreadable ({path})") from exc
        comp = rec.get("comparison", {})
        if (rec.get("state") != "PASS" or rec.get("exit_code") != 0
                or rec.get("inject") is not None or rec.get("configuration") != CONFIG
                or rec.get("scenario") != sc):
            raise ValueError(f"pads verification {sc}: not a clean PASS of {CONFIG}")
        zero = ("writes_bad", "extra_writes", "missing_writes", "hit_frame_bad",
                "boot_frame_bad", "wire_mismatch", "swap", "width", "errs", "collisions",
                "frames_no_sample", "busy_at_tick", "overrun", "x_bad")
        if (any(comp.get(k) != 0 for k in zero) or comp.get("periods", 0) < 1
                or comp.get("periods") != comp.get("periods_required")
                or comp.get("writes_seen", 0) < 1 or comp.get("presses", 0) < 1):
            raise ValueError(f"pads verification {sc}: no complete, passing comparison")
        for src in paths:
            key = str(src.relative_to(ROOT)) if src.is_relative_to(ROOT) else str(src)
            if rec.get("source_sha256", {}).get(key) != sha(src):
                raise ValueError(f"pads verification {sc}: source differs: {key}")
        transcript = path.with_name("transcript.txt")
        if not transcript.is_file() or sha(transcript) != rec.get("transcript_sha256"):
            raise ValueError(f"pads verification {sc}: transcript absent or changed")
        out[sc] = {"record_sha256": sha(path), "periods": comp["periods"],
                   "presses": comp["presses"], "writes": comp["writes_seen"]}
    return out


# ---- the verdict after Vivado ---------------------------------------------------------
_SUMMARY_NAMES = ("wns_ns", "tns_ns", "setup_failing", "setup_endpoints", "whs_ns", "ths_ns",
                  "hold_failing", "hold_endpoints", "wpws_ns", "tpws_ns", "pulse_failing",
                  "pulse_endpoints")


def timing_summary(text: str, design: str = TOP) -> dict:
    """The Design Timing Summary row of a routed timing.rpt (ValueError if the
    report is not the routed `design` or the table is not the expected shape)."""
    if not re.search(r"\| Design\s*: " + re.escape(design) + r"\s*$", text, re.MULTILINE):
        raise ValueError(f"timing report is not for {design}")
    if not re.search(r"\| Design State\s*: (Fully )?Routed\s*$", text, re.MULTILINE):
        raise ValueError("timing report is not of a routed design")
    try:
        section = text.split("| Design Timing Summary", 1)[1].split("| Clock Summary", 1)[0]
    except IndexError as exc:
        raise ValueError("timing report has no Design Timing Summary") from exc
    rows = [s.split() for s in section.splitlines() if re.match(r"^\s*[-+]?\d+\.\d+\s", s)]
    if len(rows) != 1 or len(rows[0]) != len(_SUMMARY_NAMES):
        raise ValueError("unexpected timing summary shape")
    return dict(zip(_SUMMARY_NAMES, map(float, rows[0])))


def timing_verdict(m: dict) -> tuple:
    """(ok, reasons): WNS >= 0, WHS >= 0, zero failing endpoints of each kind,
    and something was actually timed."""
    why = []
    if m["wns_ns"] < 0:
        why.append(f"WNS {m['wns_ns']} ns")
    if m["whs_ns"] < 0:
        why.append(f"WHS {m['whs_ns']} ns")
    for k in ("setup_failing", "hold_failing", "pulse_failing"):
        if m[k] != 0:
            why.append(f"{int(m[k])} {k.replace('_', ' ')} endpoint(s)")
    for k in ("setup_endpoints", "hold_endpoints"):
        if m[k] <= 0:
            why.append(f"no {k.split('_')[0]} endpoints were timed")
    return not why, why


def pads_inputs_report(text: str) -> list:
    """Problems in pads_inputs.rpt (empty = all six inputs dispositioned)."""
    got, end = {}, None
    for line in text.splitlines():
        f = line.split("\t")
        if f[0] == "INPUT" and len(f) >= 3:
            got[f[1]] = f[2]
        elif f[0] == "END" and len(f) == 2:
            end = int(f[1])
    probs = [] if end == 0 else [f"{PADS_REPORT}: END {end}"]
    for p in INPUTS:
        if got.get(p) != "1":
            probs.append(f"{PADS_REPORT}: input {p} is {got.get(p, 'absent')}")
    return probs


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=ROOT / "build/arty-pads")
    ap.add_argument("--verification", type=Path, default=ROOT / "build/pads",
                    help="fpga/verify_pads_top.py's --outdir")
    ap.add_argument("--prepare-only", action="store_true")
    ap.add_argument("--vivado", default="vivado")
    args = ap.parse_args(argv)
    directory = args.out.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    files = sources() + roms()
    report = {"state": "REFUSED", "top": TOP, "part": PART, "configuration": CONFIG,
              "release_image": False, "hardware_playback_tested": False,
              "external_io_timing_qualified": False,
              "source_sha256": {str(p.relative_to(ROOT)): sha(p) for p in files + [XDC]}}
    report_path = directory / "report.json"

    def save():
        report_path.write_text(json.dumps(report, indent=2) + "\n")
        print(report["state"], report.get("reason", report_path), flush=True)

    try:
        try:
            pr.check_rom(pr.ROM_V)
        except Exception as exc:          # Refused, KitRefused, a parse failure
            raise ValueError(f"kit-hash gate: {type(exc).__name__}: {exc}") from exc
        report["kit_sha256"] = pr.uh.R1_KIT_SHA256
        report["verification"] = validate_verification(args.verification.resolve(), files)
        snapshots = []
        for path in sources() + [XDC] + roms():
            dest = directory / "inputs" / path.relative_to(ROOT)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, dest)
            if sha(dest) != report["source_sha256"][str(path.relative_to(ROOT))]:
                raise ValueError("source changed during snapshot: " + str(path))
            snapshots.append(dest)
        n = len(sources())
        script = directory / "build.tcl"
        script.write_text(tcl_script(directory, snapshots[:n], snapshots[n]))
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
               ("arty_pads.bit", "utilization.rpt", "timing.rpt", "clocks.rpt", "drc.rpt",
                "routed.dcp", xb.REPORT, xb.EXCEPTIONS, PADS_REPORT)]
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
    report["artifact_sha256"] = {p.name: sha(p) for p in outputs if p.is_file()}
    if rc != 0 or not all(p.is_file() and p.stat().st_size > 0 for p in outputs):
        report.update(state="FAIL", reason=f"Vivado exit {rc} or an output is missing "
                      "(exit 3 = a constraint or pads-input check refused; see runner.log)")
        save()
        return 1
    problems = xb.check_report((directory / xb.REPORT).read_text(), XDC.read_text())
    problems += pads_inputs_report((directory / PADS_REPORT).read_text())
    try:
        metrics = timing_summary((directory / "timing.rpt").read_text())
        ok_t, why = timing_verdict(metrics)
        report["timing"] = metrics
        problems += why
    except ValueError as exc:
        problems.append(str(exc))
    report["problems"] = problems
    report["state"] = "PASS" if not problems else "FAIL"
    if problems:
        report["reason"] = "; ".join(problems[:4])
    save()
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
