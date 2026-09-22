#!/usr/bin/env python3
"""Run the Arty clean smoke and independent pin mutations, retaining each result."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import signal
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def caught(record, mutation):
    if record.get("state") != "FAIL" or record.get("exit_code") != 1:
        return False
    comparison = record.get("comparison", {})
    if mutation == "ARTY_MOSI_ZERO":
        return comparison.get("writes_bad", 0) > 0
    return (comparison.get("periods", 0) > 0
            and comparison.get("wire_mismatch", 0) > 0
            and comparison.get("core_bad") == 0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outdir", type=Path, default=ROOT / "build/arty-controls")
    args = parser.parse_args()
    directory = args.outdir.resolve()
    directory.mkdir(parents=True, exist_ok=True)

    def run(mutation):
        name = mutation or "clean"
        out = directory / name
        out.mkdir(parents=True, exist_ok=True)
        record_path = out / "verification.json"
        record_path.unlink(missing_ok=True)
        command = [sys.executable, str(ROOT / "fpga/verify_arty.py"), "--outdir", str(out)]
        if mutation:
            command += ["--inject", mutation]
        try:
            with (out / "runner.txt").open("w") as log:
                process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                           start_new_session=True)
                try:
                    rc = process.wait(timeout=1800)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                    raise
            record = json.loads(record_path.read_text())
            if rc != record.get("exit_code"):
                raise ValueError("process and recorded exit differ")
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            record = {"state": "REFUSED", "reason": str(exc)}
        print(name, record["state"], flush=True)
        return name, record

    with ThreadPoolExecutor(max_workers=3) as pool:
        records = dict(pool.map(run, [None, "ARTY_MOSI_ZERO", "ARTY_SDATA_ZERO"]))
    clean = records["clean"]
    baseline = clean.get("state") == "PASS" and clean.get("exit_code") == 0
    controls = {name: baseline and caught(records[name], name)
                for name in ("ARTY_MOSI_ZERO", "ARTY_SDATA_ZERO")}
    result = {"state": "PASS" if baseline and all(controls.values()) else "FAIL",
              "clean_passed": baseline, "controls_caught": controls}
    (directory / "controls.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result), flush=True)
    return 0 if result["state"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
