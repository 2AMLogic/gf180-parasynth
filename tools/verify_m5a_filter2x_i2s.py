#!/usr/bin/env python3
"""Run, hash-bind, and score the full reconstructed-2x M5A phrase at I2S."""
from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _repo_path(value: str) -> pathlib.Path:
    path = pathlib.Path(value)
    if not path.is_absolute():
        path = ROOT / path
    path = path.resolve()
    if not path.is_relative_to(ROOT):
        raise ValueError("verification outputs must remain inside the repository")
    return path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--case", choices=("M5A", "M5B"), default="M5A")
    ap.add_argument("--pulse2x", action="store_true")
    ap.add_argument("--outdir", default="build/m5a-filter2x-i2s")
    ap.add_argument("--verification", default="build/verification/M5A-filter2x-i2s.txt")
    ap.add_argument("--wav", default="build/scorecard/M5A-filter2x-i2s.wav")
    ap.add_argument("--record", default="build/scorecard/M5A-filter2x-i2s.json")
    ap.add_argument("--audio", default="build/scorecard/M5A-filter2x-i2s-measured.wav")
    ap.add_argument("--simulator", choices=("iverilog", "verilator"), default="verilator")
    ap.add_argument("--timeout", type=int, default=5400)
    a = ap.parse_args(argv)
    try:
        outdir, report, wav, record, audio = map(
            _repo_path, (a.outdir, a.verification, a.wav, a.record, a.audio))
    except ValueError as exc:
        print(f"verify_m5a_filter2x_i2s: REFUSED -- {exc}")
        return 2
    outdir.mkdir(parents=True, exist_ok=True)
    report.parent.mkdir(parents=True, exist_ok=True)
    wav.parent.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, str(ROOT / "rtl-sketch/verify_synth_top.py"),
               "--m5a", "--filter2x", "--simulator", a.simulator,
               "--wav-out", str(wav), "--outdir", str(outdir)]
    if a.case != "M5A" or a.pulse2x:
        sys.path.insert(0, str(ROOT / "tools"))
        from mono_m5a_score import MANIFESTS, engine_configuration
        engine = engine_configuration("selected")
        command += ["--m5a-manifest", str(MANIFESTS[a.case]),
                    "--m5a-saw-cutoff-hz", str(engine["saw_cutoff_hz"]),
                    "--m5a-saw-volume-correction-db", str(engine["saw_volume_correction_db"])]
    if a.pulse2x:
        command += ["--pulse2x"]
    try:
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                                timeout=a.timeout)
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or ""
        if isinstance(output, bytes):
            output = output.decode(errors="replace")
        report.write_text(output + "\nverify_m5a_filter2x_i2s: NO-VERDICT -- full RTL phrase timed out\n")
        print(f"verify_m5a_filter2x_i2s: NO-VERDICT -- timed out after {a.timeout}s; see {report}")
        return 2
    report.write_text(result.stdout + result.stderr)
    print(result.stdout, end="")
    print(result.stderr, file=sys.stderr, end="")
    if result.returncode != 0:
        print(f"verify_m5a_filter2x_i2s: integration exited {result.returncode}; score not run")
        return result.returncode if result.returncode in (1, 2) else 2
    score_command = [sys.executable, str(ROOT / "tools/score_m5a_i2s.py"),
                     "--wav", str(wav), "--verification", str(report),
                     "--out", str(record), "--audio", str(audio)]
    score_command += ["--case", a.case]
    if a.pulse2x:
        score_command += ["--pulse2x"]
    scored = subprocess.run(score_command, cwd=ROOT, text=True)
    return scored.returncode


if __name__ == "__main__":
    raise SystemExit(main())
