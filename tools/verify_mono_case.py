#!/usr/bin/env python3
"""Run the complete frozen M5A comparison and require a valid verdict.

A scorecard failure is valid evidence about the sound; a refusal, missing
metric, stale artifact or missing SPI-to-I2S check is a verification failure.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import run_case
import scorecard


def main() -> int:
    out = ROOT / "build/scorecard/m5a-verification-results"
    command = [sys.executable, str(ROOT / "tools/run_case.py"), "M5A",
               "--allow-stale", "--results", str(out)]
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
    print(result.stdout, end="")
    if result.returncode not in (0, 1):
        print(result.stderr, file=sys.stderr, end="")
        print(f"M5A verification produced no valid measurement (exit {result.returncode})",
              file=sys.stderr)
        return 2
    record_path = out / "M5A.json"
    if not record_path.is_file():
        print("M5A verification did not write its result record", file=sys.stderr)
        return 2
    record = json.loads(record_path.read_text())
    case = next(c for c in run_case.load_cases() if c["case_id"] == "M5A")
    verdict = scorecard.evaluate(case, record)
    if verdict["state"] not in (scorecard.PASS, scorecard.FAIL):
        print(f"M5A verification result is not scoreable: {verdict['why']}", file=sys.stderr)
        return 2
    expected = {m.strip() for m in case["required_measurements"].split(";") if m.strip()}
    if not expected.issubset(record.get("metrics", {})) or any(
            not record["metrics"][m].get("valid", False) for m in expected):
        print("M5A verification omitted a required measurement", file=sys.stderr)
        return 2
    diag = record.get("diagnostics", {})
    report = pathlib.Path(ROOT / record["provenance"]["artefacts"]["spi_i2s"])
    if not report.is_file() or hashlib.sha256(report.read_bytes()).hexdigest() != diag.get("spi_i2s_report_sha256"):
        print("M5A SPI-to-I2S evidence is missing or has changed", file=sys.stderr)
        return 2
    text = report.read_text()
    if "M5A path verified from SPI pins through the production voice and I2S pins" not in text:
        print("M5A SPI-to-I2S report lacks the expected end-to-end pass", file=sys.stderr)
        return 2
    print(f"M5A sound measurement is valid ({verdict['state']}); all seven metrics and "
          "the hash-bound SPI-to-I2S integration report are present.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
