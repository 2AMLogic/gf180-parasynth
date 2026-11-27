#!/usr/bin/env python3
"""Run the Arty clean smoke and independent pin mutations, retaining each result.

Exit: 0 the clean run passes and every mutation is caught for its reason, 1
when a run that executed disagreed, 2 when any child refused or did not run
(no verdict) -- controls.json then names those children under "refused".
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import signal
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
_REFUSAL_TOOLS = str(ROOT / "tools")
if _REFUSAL_TOOLS not in sys.path:
    sys.path.append(_REFUSAL_TOOLS)
from exit_contract import FAIL, PASS, REFUSED  # noqa: E402

EXIT_CODE = {"PASS": PASS, "FAIL": FAIL, "REFUSED": REFUSED}
VERDICTS = ("PASS", "FAIL")


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
    baseline = clean.get("state") == "PASS" and clean.get("exit_code") == PASS
    controls = {name: baseline and caught(records[name], name)
                for name in ("ARTY_MOSI_ZERO", "ARTY_SDATA_ZERO")}
    refused = [name for name, rec in records.items() if rec.get("state") not in VERDICTS]
    state = ("REFUSED" if refused
             else "PASS" if baseline and all(controls.values()) else "FAIL")
    result = {"state": state, "clean_passed": baseline, "controls_caught": controls}
    if refused:
        result["refused"] = refused
    (directory / "controls.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result), flush=True)
    return EXIT_CODE[state]


if __name__ == "__main__":
    raise SystemExit(main())
