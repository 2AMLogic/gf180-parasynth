#!/usr/bin/env python3
"""Build the explicit OSC2X=1 FILTER2X=1 ULX3S candidate, recording each result.

Every external process must exit successfully and produce a new nonempty
artifact. Synthesis alone never means a playable bitstream or timing pass.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
DEFINES = ("VOICE_OSC_2X", "VOICE_FILTER_2X")
_TOOLS = str(ROOT / "tools")
if _TOOLS not in sys.path:
    sys.path.append(_TOOLS)
import i2s_transcript                                             # noqa: E402

# The question that is this gate's own; whether the transcript is one clean,
# un-injected PASS from a named simulator is `i2s_transcript`'s.
M5A_SPI_I2S_VERIFIED = "M5A path verified from SPI pins through the production voice and I2S pins"
M5A_RECORD = "docs/scorecard/results/M5A.json"


def sources():
    tree = ast.parse((ROOT / "rtl-sketch/verify_synth_top.py").read_text())
    names = next(ast.literal_eval(node.value) for node in tree.body
                 if isinstance(node, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id == "SRCS" for t in node.targets))
    return [ROOT / "fpga/rtl/ulx3s_top.v"] + [ROOT / "rtl-sketch" / n for n in names]


def stored_record():
    """The fields `simulation_evidence()` reads from the committed M5A record:
    where the SPI-I2S transcript is, the digests it is bound to and the 2x paths
    it selected -- or `RuntimeError` naming the record and the class it could
    not be read as.

    A record that is not this mapping is a precondition that was not met, so it
    is REFUSED here: both callers record `RuntimeError` as exit 2, and neither
    names the classes a bare read raises (#1938). The tuple is the one
    `tools/cymbal_band_balance.py` catches for `werner_fig4.from_artifact`, and
    it stops at the read and the lookups. An absent record stays
    `FileNotFoundError`, an OSError to both callers, and what the rest of
    `simulation_evidence()` computes raises what it raises.
    """
    try:
        record = json.loads((ROOT / M5A_RECORD).read_text())
        provenance = record["provenance"]
        inputs = provenance["inputs"]
        decoded = inputs.get("decoded:I2S")
        return {"transcript": ROOT / provenance["artefacts"]["spi_i2s"],
                "transcript_sha256": inputs["verification:SPI-I2S"].removeprefix("sha256:"),
                "decoded": decoded,
                "wav_sha256": None if decoded is None else decoded.removeprefix("sha256:"),
                "config": {name: provenance["config"][name]
                           for name in ("oscillator_oversample_2x", "filter_rate_converted")}}
    except (json.JSONDecodeError, UnicodeDecodeError, KeyError, TypeError, AttributeError) as exc:
        raise RuntimeError(f"{M5A_RECORD} is not a readable M5A record "
                           f"({type(exc).__name__}: {exc})") from None


def simulation_evidence():
    stored = stored_record()
    transcript = stored["transcript"]
    content = transcript.read_bytes()
    expected = stored["transcript_sha256"]
    if hashlib.sha256(content).hexdigest() != expected:
        raise RuntimeError("stored SPI-I2S transcript hash differs")
    config = stored["config"]
    if not config["oscillator_oversample_2x"] or not config["filter_rate_converted"]:
        raise RuntimeError("stored simulation does not select both 2x paths")
    text = content.decode("utf-8", errors="replace")
    decoded = stored["decoded"]
    if not decoded:
        raise RuntimeError("stored simulation records no decoded I2S WAV digest")
    i2s_transcript.validate_clean_pass(text, wav_sha256=stored["wav_sha256"], refuse=RuntimeError)
    if M5A_SPI_I2S_VERIFIED not in text:
        raise RuntimeError("stored SPI-I2S transcript does not verify the M5A path from the SPI pins")
    stamp = re.search(r"built from ([0-9a-f]+)(-dirty)?", text)
    if stamp is None:
        raise RuntimeError("stored SPI-I2S transcript does not say which commit built it")
    if stamp.group(2):
        raise RuntimeError(f"stored SPI-I2S transcript was built from a dirty tree: {stamp.group(1)}-dirty")
    commit = stamp.group(1)
    files = sources()[1:] + sorted((ROOT / "rtl-sketch").glob("*.hex"))
    for path in files:
        old = subprocess.check_output(["git", "show", f"{commit}:{path.relative_to(ROOT)}"],
                                      cwd=ROOT, stderr=subprocess.PIPE)
        if old != path.read_bytes():
            raise RuntimeError(f"{path.name} differs from recorded simulation {commit}")
    return {"commit": commit, "transcript": str(transcript.relative_to(ROOT)),
            "sha256": expected, "core_files_and_roms_identical": True,
            "scope": "reused complete-phrase SPI-to-I2S core simulation; FPGA wrapper is not covered"}


def child_failure(exc: BaseException) -> str:
    """The reason for a child that failed: what str() says -- the command and
    its status, or the timeout -- and then what the child said on stderr, which
    str() leaves out and which is where git says why. stderr is bytes when the
    call had no text=True and None when it was not captured. Anything that is
    not a failed child reads as str() has always rendered it."""
    if not isinstance(exc, (subprocess.CalledProcessError, subprocess.TimeoutExpired)):
        return str(exc)
    said = exc.stderr
    if isinstance(said, bytes):
        said = said.decode(errors="replace")
    return f"{exc}" + (f"\n{said.strip()[:2000]}" if said and said.strip() else "")


def run_stage(command, cwd, log, artifact):
    artifact.unlink(missing_ok=True)
    started = time.monotonic()
    try:
        with log.open("w") as output:
            completed = subprocess.run(command, cwd=cwd, stdout=output, stderr=subprocess.STDOUT)
        rc = completed.returncode
    except OSError as exc:
        return {"state": "REFUSED", "command": command, "reason": str(exc)}
    exists = artifact.exists() and artifact.stat().st_size > 0
    return {"state": "PASS" if rc == 0 and exists else "FAIL", "exit_code": rc,
            "command": command, "seconds": round(time.monotonic() - started, 3),
            "log": str(log), "artifact": str(artifact),
            "artifact_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest() if exists else None}


def reuse_synthesis(path, current):
    """Reuse a successful netlist only after rechecking its exact inputs/bytes."""
    prior = json.loads(Path(path).read_text())
    if prior.get("configuration") != current["configuration"]:
        raise RuntimeError("saved synthesis configuration differs")
    if prior.get("source_sha256") != current["source_sha256"]:
        raise RuntimeError("saved synthesis source hashes differ")
    stage = prior.get("stages", {}).get("synthesis", {})
    artifact = Path(stage.get("artifact", ""))
    if (stage.get("state") != "PASS" or stage.get("exit_code") != 0
            or not artifact.is_file() or artifact.stat().st_size == 0
            or hashlib.sha256(artifact.read_bytes()).hexdigest() != stage.get("artifact_sha256")):
        raise RuntimeError("saved synthesis netlist is absent, changed or unsuccessful")
    return {**stage, "reused_from_report": str(Path(path).resolve())}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "build/fpga-selected")
    parser.add_argument("--nextpnr", default="nextpnr-ecp5")
    parser.add_argument("--device", choices=("25k", "45k", "85k"), default="85k")
    parser.add_argument("--from-synthesis", type=Path,
                        help="reuse a hash-verified successful synthesis report")
    parser.add_argument("--synth-only", action="store_true")
    args = parser.parse_args(argv)
    directory = args.out.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    source_files = sources()
    report = {"configuration": {"OSC2X": 1, "FILTER2X": 1, "pulse_duty_percent": 47.90},
              "source_commit": None,
              "source_sha256": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in source_files},
              "device": args.device, "package": "CABGA381",
              "stages": {}, "bitstream_built": False, "hardware_playback_tested": False}
    report_path = directory / "report.json"

    def save():
        report_path.write_text(json.dumps(report, indent=2) + "\n")

    try:
        report["source_commit"] = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.PIPE).strip()
        report["simulation_evidence"] = simulation_evidence()
        report["yosys_version"] = subprocess.check_output(["yosys", "-V"], text=True).strip()
        if args.from_synthesis:
            report["stages"]["synthesis"] = reuse_synthesis(args.from_synthesis, report)
    except (OSError, subprocess.CalledProcessError, RuntimeError) as exc:
        report.update(state="REFUSED", reason=child_failure(exc))
        save(); print(report["reason"]); return 2
    quoted_sources = " ".join(f'"{p}"' for p in source_files)
    synth = ("read_verilog -defer " + " ".join(f"-D{d}" for d in DEFINES) + " " + quoted_sources
             + f'; synth_ecp5 -top ulx3s_top -json "{directory / "ecp5.json"}"; stat -top ulx3s_top')
    jobs = [] if args.from_synthesis else [
        ("synthesis", ["yosys", "-p", synth], ROOT / "rtl-sketch", directory / "ecp5.json")]
    netlist = (Path(report["stages"]["synthesis"]["artifact"]) if args.from_synthesis
               else directory / "ecp5.json")
    if not args.synth_only:
        jobs.extend([
            ("place_route", [args.nextpnr, "--" + args.device, "--package", "CABGA381", "--json", str(netlist),
             "--lpf", str(ROOT / "fpga/boards/ulx3s.lpf"), "--seed", "1", "--textcfg", str(directory / "ecp5.config"),
             "--report", str(directory / "timing.json")], directory, directory / "ecp5.config"),
            ("bitstream", ["ecppack", str(directory / "ecp5.config"), str(directory / "ecp5.bit")], directory, directory / "ecp5.bit")])
    for name, command, cwd, artifact in jobs:
        result = run_stage(command, cwd, directory / f"{name}.log", artifact)
        report["stages"][name] = result
        report["state"] = result["state"]
        save(); print(name, result["state"], flush=True)
        if result["state"] != "PASS":
            log = directory / f"{name}.log"
            if log.exists(): print("\n".join(log.read_text(errors="replace").splitlines()[-15:]))
            return 2 if result["state"] == "REFUSED" else 1
    report["state"] = "SYNTHESIZED" if args.synth_only else "BUILT"
    report["bitstream_built"] = not args.synth_only
    save(); print(f"{report['state']}: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
