"""Validate and preserve the completed 85F CI artifact, including failures."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys

import build_selected

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import setup_ci_oss_cad as toolchain_pin


def timing_summary(report):
    clocks = report.get("fmax", {})
    core = {name: value for name, value in clocks.items() if "clk_core" in name}
    if len(core) != 1 or not report.get("critical_paths") or not report.get("utilization"):
        raise ValueError("timing report lacks core clock, paths or utilization")
    target = next(iter(core.values()))["constraint"]
    # Pinned nextpnr 3e53a0bf, ecp5/arch.h:976 and pack.cc:2852:
    # derived clock periods are truncated to integer picoseconds, then the
    # report converts them back through float32 nanoseconds/frequency. Accept
    # the exact PLL period or that single quantized period; not a broad ppm
    # frequency allowance. 0.01 ps covers float32 roundoff (fixture: 0.005 ps).
    ideal_period_ps = 1e6 / (725 / 59)
    period_ps = 1e6 / target if math.isfinite(target) and target > 0 else math.nan
    quantized_period_ps = math.floor(ideal_period_ps)
    if not any(math.isclose(period_ps, expected, abs_tol=.01, rel_tol=0)
               for expected in (ideal_period_ps, quantized_period_ps)):
        raise ValueError("core clock constraint is not the selected PLL rate")
    for name, clock in clocks.items():
        if (not all(math.isfinite(clock[k]) and clock[k] > 0 for k in ("constraint", "achieved"))
                or clock["achieved"] < clock["constraint"]):
            raise ValueError(f"timing does not pass: {name}")
    for name, count in report["utilization"].items():
        if not 0 <= count["used"] <= count["available"]:
            raise ValueError(f"resource count does not fit: {name}")
    return {"state": "PASS", "core_constraint_mhz": target,
            "core_period_identity": {"ideal_ps": ideal_period_ps, "reported_ps": period_ps,
                                     "nextpnr_integer_ps": quantized_period_ps,
                                     "float_conversion_tolerance_ps": .01}, "clocks": clocks,
            "utilization": report["utilization"]}


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def publish(artifact: Path, output: Path, workflow_url: str, *, historical=False):
    if output.exists() and any(output.iterdir()):
        raise ValueError("publication directory must be empty; refuse stale artifacts")
    reports = list(artifact.rglob("report.json"))
    toolchains = list(artifact.rglob("toolchain.json"))
    if len(reports) != 1 or len(toolchains) != 1:
        raise ValueError("expected one build report and one pinned toolchain record")
    report = json.loads(reports[0].read_text())
    toolchain = json.loads(toolchains[0].read_text())
    if toolchain["sha256"] != toolchain_pin.SHA256 or toolchain["release"] != toolchain_pin.VERSION:
        raise ValueError("toolchain identity does not match the pinned suite")
    build = reports[0].parent
    expected_config = {"OSC2X": 1, "FILTER2X": 1, "pulse_duty_percent": 47.90}
    if report["configuration"] != expected_config or report["device"] != "85k":
        raise ValueError("artifact is not the selected 85F baseline")
    commit = report["source_commit"]
    changed_sources = []
    for name, digest in report["source_sha256"].items():
        saved = subprocess.check_output(["git", "show", f"{commit}:{name}"], cwd=ROOT)
        if hashlib.sha256(saved).hexdigest() != digest:
            raise ValueError(f"build source differs: {name}")
        if not (ROOT / name).is_file() or sha256(ROOT / name) != digest:
            changed_sources.append(name)
            if not historical:
                raise ValueError(f"current source differs: {name}; use --historical for the recorded build")
    # LPF/ROM contents are also build inputs, though the original runner's
    # source map records Verilog only. Bind them to the CI source commit here.
    inputs = dict(report["source_sha256"])
    for path in [ROOT / "fpga/boards/ulx3s.lpf", *sorted((ROOT / "rtl-sketch").glob("*.hex"))]:
        name = str(path.relative_to(ROOT))
        saved = subprocess.check_output(["git", "show", f"{commit}:{name}"], cwd=ROOT)
        if path.read_bytes() != saved and not historical:
            raise ValueError(f"build input differs: {name}")
        inputs[name] = hashlib.sha256(saved).hexdigest()
    if historical:
        simulation = report["simulation_evidence"]
        transcript = subprocess.check_output(["git", "show", f"{commit}:{simulation['transcript']}"], cwd=ROOT)
        if hashlib.sha256(transcript).hexdigest() != simulation["sha256"]:
            raise ValueError("historical simulation transcript differs")
        for name, digest in inputs.items():
            if name.startswith("rtl-sketch/"):
                simulated = subprocess.check_output(["git", "show", f"{simulation['commit']}:{name}"], cwd=ROOT)
                if hashlib.sha256(simulated).hexdigest() != digest:
                    raise ValueError(f"historical core differs from simulation: {name}")
    else:
        simulation = build_selected.simulation_evidence()
    if simulation != report["simulation_evidence"]:
        raise ValueError("core simulation binding differs")
    original_hashes = {p.name: sha256(p) for p in build.iterdir() if p.is_file()}
    for stage in report["stages"].values():
        digest = stage.get("artifact_sha256")
        if digest and original_hashes.get(Path(stage["artifact"]).name) != digest:
            raise ValueError("stage artifact hash differs")
    summary = {"workflow_url": workflow_url, "source_commit": commit,
               "configuration": {"OSC2X": 1, "FILTER2X": 1, "PULSE2X": 0},
               "device": "85k", "package": report["package"],
               "hardware_playback_tested": False, "simulation_evidence": simulation,
               "source_sha256": inputs, "original_artifact_sha256": original_hashes,
               "source_basis": "historical recorded build" if historical else "current checkout matches recorded build",
               "current_checkout_differences": changed_sources,
               "state": "NO-VERDICT", "bitstream_qualified": False}
    try:
        if report["state"] != "BUILT" or not report["bitstream_built"]:
            raise ValueError(f"build did not finish successfully: {report['state']}")
        if set(report["stages"]) != {"synthesis", "place_route", "bitstream"}:
            raise ValueError("build stages incomplete")
        for stage in report["stages"].values():
            if stage["state"] != "PASS" or stage["exit_code"] != 0:
                raise ValueError("a build stage did not pass")
        summary["timing"] = timing_summary(json.loads((build / "timing.json").read_text()))
        if not (build / "ecp5.bit").is_file() or not (build / "ecp5.bit").stat().st_size:
            raise ValueError("bitstream absent")
        summary.update(state="BUILT-TIMING-PASS", bitstream_qualified=True,
                       bitstream_sha256=sha256(build / "ecp5.bit"))
    except (ValueError, FileNotFoundError, KeyError) as exc:
        summary["reason"] = str(exc)
        if report["state"] == "FAIL":
            summary["state"] = "FAIL"
    output.mkdir(parents=True, exist_ok=True)
    for name in ("report.json", "timing.json", "ecp5.bit"):
        if (build / name).is_file():
            shutil.copyfile(build / name, output / name)
    shutil.copyfile(toolchains[0], output / "toolchain.json")
    for path in build.glob("*.log"):
        (output / (path.name + ".gz")).write_bytes(gzip.compress(path.read_bytes(), mtime=0))
    summary["published_sha256"] = {p.name: sha256(p) for p in output.iterdir()
                                   if p.is_file() and p.name != "publication.json"}
    (output / "publication.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--workflow-url", required=True)
    parser.add_argument("--out", type=Path, default=ROOT / "fpga/reports/selected/linux-85f")
    parser.add_argument("--historical", action="store_true",
                        help="verify the recorded source commit; do not claim the current checkout was built")
    args = parser.parse_args()
    result = publish(args.artifact, args.out, args.workflow_url, historical=args.historical)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["bitstream_qualified"] else 2)
