#!/usr/bin/env python3
"""Build the Arty no-DAC demo image (fpga/rtl/arty_a7_sd_top.v, #406).

    python3 fpga/build_arty_sd.py [--out build/arty-sd] [--prepare-only]

A DEMO IMAGE, NEVER PUBLISHED. publish_arty.VERIFICATION_BY_WRAPPER has no
entry for arty_a7_sd_top, so the release publisher refuses it; this script is
the whole of its build record. Decided in #406: the demo wrapper is not scoped
into the release gate (`output_delay_exceptions == ["i2s_bclk"]`); instead its
own gate below requires exactly that forwarded-clock exception AND exactly
the two PDM ports as user false paths, with nothing unconstrained; the
constant dac_sck must be in no timing class and named constant by synthesis. The image lays JA out for a PCM5102 breakout plugged straight in
(see arty_a7_sd_top.v), and the gate reads the ROUTED design's report_io to
confirm every port sits on the pin that layout needs.

Before Vivado runs, two digital proofs must bind to the live tree, or the
build is REFUSED:

  * the core wrapper's proof -- publish_arty.VERIFICATION_BY_WRAPPER
    ["arty_a7_top"], validated exactly as build_arty.py validates it (the SD
    wrapper instantiates arty_a7_top unchanged);
  * the no-DAC path's proof -- a PASS from fpga/verify_sd_dac.py whose
    source hashes match the live i2s_rx.v, sd_dac.v, bench and i2s_tx.v.

After Vivado: the routed reports must show internal timing passing (WNS >= 0,
zero failing endpoints; publish_arty.inspect_reports), every XDC object query
bound (xdc_bindings), and the output-delay classes exactly as above. The
resulting state is BUILT_DEMO_TIMING_PASS, or FAIL / REFUSED with a reason.

Runs on the x86-64 Linux build box with Vivado 2025.1 on PATH, like
build_arty.py. The .bit goes to the machine the board is on:
`openFPGALoader -b arty_a7_100t build/arty-sd/arty.bit` (SRAM) or `-f` (flash).
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import time

import build_arty as build
import publish_arty as publish
import xdc_bindings as xb

ROOT = build.ROOT
TOP = "arty_a7_sd_top"
SD_XDC = ROOT / "fpga/boards/arty-a7-100-sd.xdc"
PDM_PORTS = ["sd_left", "sd_right"]
CONSTANT_PORTS = ["dac_sck"]     # tied off by synthesis; timed by nothing
FORWARDED_CLOCKS = ["i2s_bclk"]
# JA as the plugged-in PCM5102 needs it, written from the BREAKOUT'S header
# order (VIN GND LCK DIN BCK SCK into JA6..JA1) and Digilent's master XDC --
# not read back from our XDC, so a wrong override cannot agree with itself.
DIRECT_PLUG_PINS = {"dac_sck": "G13", "i2s_bclk": "B11", "i2s_sdata": "A11",
                    "i2s_lrclk": "D12", "sd_left": "D4", "sd_right": "D3"}


def sources():
    return ([ROOT / "fpga/rtl/arty_a7_sd_top.v", ROOT / "fpga/rtl/i2s_rx.v",
             ROOT / "fpga/rtl/sd_dac.v"] + build.sources())


def validate_sd_verification(path: Path) -> dict:
    import verify_sd_dac as vsd
    try:
        record = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise ValueError("missing or unreadable no-DAC verification: " + str(path)) from exc
    if record.get("state") != "PASS":
        raise ValueError("no-DAC verification is not a PASS")
    if not all(c.get("caught") for c in record.get("controls", {}).values()) \
            or set(record.get("controls", {})) != set(vsd.CONTROLS):
        raise ValueError("no-DAC verification did not catch every control")
    for p in vsd.SOURCES:
        key = str(p.relative_to(ROOT))
        if record.get("source_sha256", {}).get(key) != build.sha(p):
            raise ValueError("no-DAC verification source differs: " + key)
    return {"record_sha256": build.sha(path),
            "snr_db": {k: v.get("snr_db") for k, v in record["clean"].items()}}


def _listed_ports(timing: str, marker: str, count: int) -> list:
    if not count:
        return []
    listed = re.search(re.escape(marker) + r"[^\n]*\n\n(.*?\n)\n", timing, re.DOTALL)
    if listed is None or len(listed[1].split()) != count:
        raise ValueError("ports are not listed under: " + marker)
    return sorted(listed[1].split())


def output_disposition(timing: str) -> dict:
    """The routed report's no_output_delay classes, by port name. Refuses a
    report that does not classify them (the tool changed its wording)."""
    def count(marker):
        m = re.search(r"There (?:are|is) (\d+) ports? " + re.escape(marker), timing)
        if m is None:
            raise ValueError("report does not classify no_output_delay ports: " + marker)
        return int(m[1])
    classes = {}
    for key, marker in (("unconstrained", "with no output delay specified"),
                        ("false_path", "with no output delay but user has a false path constraint"),
                        ("forwarded_clock", "with no output delay but with a timing clock defined on it")):
        classes[key] = _listed_ports(timing, marker, count(marker))
    return classes


def constant_port_evidence(log_text: str, port: str) -> bool:
    """True iff Vivado's own Synth 8-3917 warning says `port` is tied to a
    constant. The single source of truth for this pattern -- check_implementation
    (the build gate) and its test both call this function, so a change or
    breakage in the pattern shows up in one place, not two independently
    written copies that can drift apart."""
    return re.search(r"Synth 8-3917\].* port " + re.escape(port) + r" driven by constant 0",
                     log_text) is not None


def check_implementation(directory: Path) -> dict:
    summary = publish.inspect_reports(directory, design=TOP)
    # the constraints this build COMPILED (its snapshot), never the live tree's
    xdc_text = "".join((directory / "inputs" / p.relative_to(ROOT)).read_text()
                       for p in (build.XDC, SD_XDC))
    problems = xb.check_report((directory / xb.REPORT).read_text(), xdc_text)
    problems += xb.check_route((directory / xb.REPORT).read_text(),
                               (directory / xb.EXCEPTIONS).read_text(), xdc_text)
    classes = output_disposition((directory / "timing.rpt").read_text())
    want = {"unconstrained": [], "false_path": PDM_PORTS,
            "forwarded_clock": FORWARDED_CLOCKS}
    if classes != want:
        problems.append(f"output-port disposition {classes} is not {want}")
    # a constant port is dispositioned by evidence that it IS constant: the
    # synthesis log must say so, and no timing class may hold it
    log = (directory / "vivado.log").read_text(errors="replace")
    for port in CONSTANT_PORTS:
        if not constant_port_evidence(log, port):
            problems.append(f"{port} is not shown constant by the synthesis log")
    pins = placed_pins((directory / IO_REPORT).read_text(), DIRECT_PLUG_PINS)
    if pins != DIRECT_PLUG_PINS:
        problems.append(f"placed pins {pins} are not the direct-plug map {DIRECT_PLUG_PINS}")
    if problems:
        raise ValueError("; ".join(problems))
    return {"timing": summary["timing"], "resources": summary["resources"],
            "drc": summary["drc"], "output_disposition": classes, "placed_pins": pins}


IO_REPORT = "io.rpt"


_PACKAGE_PIN_RE = re.compile(r"^set_property PACKAGE_PIN (\w+) "
                             r"\[get_ports \{?([\w\[\]]+)\}?\]")


def effective_pins(xdc_texts: list) -> dict:
    """port -> PACKAGE_PIN after the XDCs apply in order (a later set wins).
    Raises if any assignment moves a port onto a site another port holds at
    that moment -- the order the demo XDC's overrides must respect.
    Also raises on any `set_property` line that names PACKAGE_PIN but does
    not match the single-line form above (e.g. Vivado's `-dict { PACKAGE_PIN
    ... }` form) instead of silently dropping it from the map -- an
    unsupported form must fail loud, not vanish (#471)."""
    pins = {}
    for text in xdc_texts:
        for line in text.splitlines():
            if not line.startswith("set_property") or "PACKAGE_PIN" not in line:
                continue
            match = _PACKAGE_PIN_RE.match(line)
            if match is None:
                raise ValueError(
                    "unparseable PACKAGE_PIN assignment -- only the single-line "
                    "'set_property PACKAGE_PIN <site> [get_ports <port>]' form "
                    f"is understood (e.g. Vivado's -dict form is not): {line!r}")
            site, port = match.group(1), match.group(2)
            holder = next((p for p, s_ in pins.items() if s_ == site and p != port), None)
            if holder:
                raise ValueError(f"{port} moved onto {site} while {holder} holds it")
            pins[port] = site
    return pins


def placed_pins(io_report: str, ports) -> dict:
    """port -> package pin from the routed design's report_io table."""
    out = {}
    for line in io_report.splitlines():
        cells = [c.strip() for c in line.split("|")]
        for port in ports:
            if port in cells:
                site = next((c for c in cells if re.fullmatch(r"[A-Z]{1,2}\d{1,2}", c)), None)
                if site:
                    out[port] = site
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", type=Path, default=ROOT / "build/arty-sd")
    parser.add_argument("--verification", type=Path,
                        default=publish.VERIFICATION_BY_WRAPPER["arty_a7_top"])
    parser.add_argument("--sd-verification", type=Path,
                        default=ROOT / "build/sd-dac/verification.json")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--vivado", default="vivado")
    args = parser.parse_args(argv)
    directory = args.out.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    constraints = [build.XDC, SD_XDC]
    report = {"state": "REFUSED", "top": TOP, "part": build.PART,
              "configuration": build.CONFIG, "published": False,
              "source_sha256": {str(p.relative_to(ROOT)): build.sha(p)
                                for p in sources() + build.roms() + constraints}}
    report_path = directory / "report.json"

    def save():
        report_path.write_text(json.dumps(report, indent=2) + "\n")
        print(report["state"], report.get("reason", report_path), flush=True)

    try:
        report["verification"] = build.validate_verification(
            args.verification, build.sources() + build.roms())
        report["sd_verification"] = validate_sd_verification(args.sd_verification)
        snapshots = []
        for path in sources() + build.roms() + constraints:
            dest = directory / "inputs" / path.relative_to(ROOT)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, dest)
            if build.sha(dest) != report["source_sha256"][str(path.relative_to(ROOT))]:
                raise ValueError("source changed during snapshot: " + str(path))
            snapshots.append(dest)
        n = len(sources())
        verilog, xdcs = snapshots[:n], snapshots[-len(constraints):]
        script = directory / "build.tcl"
        # the pins the ROUTED design actually used, for the direct-plug check
        effective_pins([x.read_text() for x in xdcs])     # refuses an unsafe order
        text = build.tcl_script(directory, verilog, xdcs[0], top=TOP,
                                extra_constraints=xdcs[1:])
        text = text.replace("write_bitstream", "report_io -file "
                            + build.tcl_word(directory / IO_REPORT) + "\nwrite_bitstream", 1)
        script.write_text(text)
        report["script_sha256"] = build.sha(script)
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
               ("arty.bit", "utilization.rpt", "timing.rpt", "clocks.rpt", "drc.rpt",
                "routed.dcp", xb.REPORT, xb.EXCEPTIONS, IO_REPORT)]
    for path in outputs:
        path.unlink(missing_ok=True)
    started = time.monotonic()
    command = [executable, "-mode", "batch", "-source", str(script),
               "-log", str(directory / "vivado.log"), "-journal", str(directory / "vivado.jou")]
    report["command"] = command
    with (directory / "runner.log").open("w") as log:
        process = subprocess.Popen(command, cwd=directory / "inputs/rtl-sketch",
                                   stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            rc = process.wait(timeout=7200)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            report.update(reason="Vivado exceeded 2 hours", state="REFUSED")
            save()
            return 2
    report.update(exit_code=rc, seconds=round(time.monotonic() - started, 3))
    report["artifact_sha256"] = {p.name: build.sha(p) for p in outputs if p.is_file()}
    if rc != 0 or not all(p.is_file() and p.stat().st_size > 0 for p in outputs):
        report.update(state="FAIL", reason=f"Vivado exit {rc} or an output is missing")
        save()
        return 1
    try:
        report["implementation"] = check_implementation(directory)
    except ValueError as exc:
        report.update(state="FAIL", reason=str(exc))
        save()
        return 1
    report["state"] = "BUILT_DEMO_TIMING_PASS"
    save()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
