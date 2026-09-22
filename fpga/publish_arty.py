"""Publish routed Arty evidence, preserving unqualified I/O and DSP warnings."""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path
import re
import shutil
import build_arty as build

ROOT = Path(__file__).resolve().parents[1]


def inspect_reports(directory):
    try:
        texts = {name: (directory / name).read_text() for name in
                 ("timing.rpt", "clocks.rpt", "utilization.rpt", "drc.rpt")}
    except OSError as exc:
        raise ValueError("missing implementation report") from exc
    timing = texts["timing.rpt"]
    for content in texts.values():
        if (not re.search(r"\| Design\s*: arty_a7_top\s*$", content, re.MULTILINE)
                or not re.search(r"\| Design State\s*: (Fully )?Routed\s*$", content, re.MULTILINE)):
            raise ValueError("report is not the routed Arty design")
    try:
        section = timing.split("| Design Timing Summary", 1)[1].split("| Clock Summary", 1)[0]
        rows = [s.split() for s in section.splitlines() if re.match(r"^\s*[-+]?\d+\.\d+\s", s)]
        if len(rows) != 1 or len(rows[0]) != 12:
            raise ValueError("unexpected timing summary shape")
        names = ("wns_ns", "tns_ns", "setup_failing", "setup_endpoints", "whs_ns", "ths_ns",
                 "hold_failing", "hold_endpoints", "wpws_ns", "tpws_ns", "pulse_failing", "pulse_endpoints")
        metrics = dict(zip(names, map(float, rows[0])))
        for kind in ("no_clock", "unconstrained_internal_endpoints", "loops", "generated_clocks"):
            counts = re.findall(r"checking " + kind + r" \((\d+)\)", timing)
            if not counts or any(int(n) for n in counts):
                raise ValueError("internal timing is incomplete: " + kind)
        missing = int(re.search(r"checking no_output_delay \((\d+)\)", timing)[1])
        match = re.search(r"^hardware_clock\.clock_raw\s+([\d.]+)\s+.*P,G,A\s+"
                          r"\{hardware_clock\.mmcm/CLKOUT0\}", texts["clocks.rpt"], re.MULTILINE)
        period = float(match[1])
        if not math.isclose(period, 1000 / 12.288, abs_tol=0.0005, rel_tol=0):
            raise ValueError("wrong core clock period")
        resources = {}
        for name in ("Slice LUTs", "Slice Registers", "DSPs", "Block RAM Tile", "Bonded IOB"):
            row = re.search(r"^\|\s*" + re.escape(name) + r"\s*\|([^\n]+)",
                            texts["utilization.rpt"], re.MULTILINE)
            columns = [float(s.strip()) for s in row[1].split("|") if s.strip()]
            used, available = columns[0], columns[3]
            if not 0 <= used <= available:
                raise ValueError("resource overflow: " + name)
            resources[name] = {"used": used, "available": available}
        drc = {m[1]: {"severity": m[2].strip(), "count": int(m[3])} for m in re.finditer(
            r"^\|\s*([A-Z][A-Z0-9-]+)\s*\|\s*([^|]+)\|[^|]+\|\s*(\d+)\s*\|",
            texts["drc.rpt"], re.MULTILINE)}
        total = int(re.search(r"Checks found:\s*(\d+)", texts["drc.rpt"])[1])
        if sum(v["count"] for v in drc.values()) != total:
            raise ValueError("DRC table incomplete")
    except (IndexError, TypeError, AttributeError) as exc:
        raise ValueError("unrecognized implementation report") from exc
    if (not all(math.isfinite(v) and v >= 0 for v in metrics.values())
            or any(metrics[k] != 0 for k in ("tns_ns", "ths_ns", "tpws_ns", "setup_failing",
                                             "hold_failing", "pulse_failing"))
            or any(metrics[k] <= 0 for k in ("setup_endpoints", "hold_endpoints", "pulse_endpoints"))):
        raise ValueError("internal timing does not pass")
    if any(v["severity"] not in ("Warning", "Advisory") for v in drc.values()):
        raise ValueError("DRC error or critical warning")
    return {"internal_timing_pass": True, "timing": metrics, "core_period_ns": period,
            "resources": resources, "missing_output_delays": missing, "drc": drc,
            # External I/O timing counts as qualified only when the routed
            # report itself shows every output port carrying an output delay;
            # the timing metrics above already exclude any failing endpoint.
            "external_io_timing_qualified": missing == 0,
            "dsp_feedback_review_complete": False,
            "hardware_playback_tested": False}


def publish(artifact, output):
    record = json.loads((artifact / "report.json").read_text())
    expected = {str(p.relative_to(ROOT)): build.sha(p)
                for p in build.sources() + build.roms() + [build.XDC]}
    if (record.get("state") != "BUILT_REQUIRES_TIMING_REVIEW" or record.get("exit_code") != 0
            or record.get("part") != build.PART or record.get("configuration") != build.CONFIG
            or record.get("source_sha256") != expected):
        raise ValueError("build is not a successful, current, selected Arty implementation")
    proof = build.validate_verification(ROOT / "fpga/reports/arty/clean/verification.json",
                                       build.sources() + build.roms())
    if proof != record.get("verification"):
        raise ValueError("digital verification binding differs")
    required = {"arty.bit", "timing.rpt", "clocks.rpt", "utilization.rpt", "drc.rpt", "routed.dcp"}
    if set(record.get("artifact_sha256", {})) != required:
        raise ValueError("build artifact set incomplete")
    for name, digest in record["artifact_sha256"].items():
        path = artifact / name
        if not path.is_file() or not path.stat().st_size or build.sha(path) != digest:
            raise ValueError("build artifact missing or changed: " + name)
    if build.sha(artifact / "build.tcl") != record["script_sha256"]:
        raise ValueError("build script changed")
    summary = inspect_reports(artifact)
    remaining = ["physical programming, control and audio capture",
                 f"{summary['drc'].get('DPREG-4', {}).get('count', 0)} DPREG-4 DSP feedback warnings"]
    if summary["external_io_timing_qualified"]:
        remaining.insert(0, "spi_miso status readback is qualified only at SCK <= 1.4 MHz, "
                            "not at the 2.0 MHz write ceiling (fpga/ext_io_timing.py)")
    else:
        remaining.insert(0, "DAC/controller output timing")
    summary.update(state="BUILT_INTERNAL_TIMING_PASS_REVIEW_REQUIRED", configuration=build.CONFIG,
                   part=build.PART, bitstream_sha256=record["artifact_sha256"]["arty.bit"],
                   source_sha256=expected, verification=proof,
                   tool=record["vivado_version"], build_seconds=record["seconds"],
                   original_artifact_sha256=record["artifact_sha256"],
                   report_transformation="Host header omitted; numerical report contents unchanged",
                   remaining_review=remaining)
    manifest = artifact.parent / "input-bundle.json"
    if manifest.is_file():
        summary["input_bundle"] = json.loads(manifest.read_text())
    if output.exists() and any(output.iterdir()):
        raise ValueError("publication directory must be empty")
    output.mkdir(parents=True, exist_ok=True)
    for name in ("report.json", "build.tcl", "arty.bit"):
        shutil.copyfile(artifact / name, output / name)
    for name in required - {"arty.bit", "routed.dcp"}:
        content = (artifact / name).read_text()
        content = re.sub(r"^\| Host\s*:.*$", "| Host         : omitted from public report",
                         content, flags=re.MULTILINE)
        (output / name).write_text(content)
    summary["published_sha256"] = {p.name: build.sha(p) for p in output.iterdir()}
    (output / "publication.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(publish(args.artifact, args.out), indent=2))
