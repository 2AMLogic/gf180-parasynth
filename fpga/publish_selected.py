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
    if not math.isclose(target, 725 / 59, abs_tol=1e-5, rel_tol=0):
        raise ValueError("core clock constraint is not the selected PLL rate")
    for name, clock in clocks.items():
        if (not all(math.isfinite(clock[k]) and clock[k] > 0 for k in ("constraint", "achieved"))
                or clock["achieved"] < clock["constraint"]):
            raise ValueError(f"timing does not pass: {name}")
    for name, count in report["utilization"].items():
        if not 0 <= count["used"] <= count["available"]:
            raise ValueError(f"resource count does not fit: {name}")
    return {"state": "PASS", "core_constraint_mhz": target, "clocks": clocks,
            "utilization": report["utilization"]}


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def publish(artifact: Path, output: Path, workflow_url: str):
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
    for name, digest in report["source_sha256"].items():
        saved = subprocess.check_output(["git", "show", f"{commit}:{name}"], cwd=ROOT)
        if hashlib.sha256(saved).hexdigest() != digest or sha256(ROOT / name) != digest:
            raise ValueError(f"build source differs: {name}")
    # LPF/ROM contents are also build inputs, though the original runner's
    # source map records Verilog only. Bind them to the CI source commit here.
    inputs = dict(report["source_sha256"])
    for path in [ROOT / "fpga/boards/ulx3s.lpf", *sorted((ROOT / "rtl-sketch").glob("*.hex"))]:
        name = str(path.relative_to(ROOT))
        saved = subprocess.check_output(["git", "show", f"{commit}:{name}"], cwd=ROOT)
        if path.read_bytes() != saved:
            raise ValueError(f"build input differs: {name}")
        inputs[name] = sha256(path)
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
    args = parser.parse_args()
    result = publish(args.artifact, args.out, args.workflow_url)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["bitstream_qualified"] else 2)
