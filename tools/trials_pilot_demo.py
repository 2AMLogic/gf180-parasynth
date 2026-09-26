#!/usr/bin/env python3
"""Produce the pilot's REAL receipts: plan085 section 5 acceptance, exercised on
the real checkers of this tree (build box; not the laptop -- CLAUDE.md).

    python tools/trials_pilot_demo.py --venv ~/work/trials-venv

What it does, and what each step demonstrates:

  bootstrap x2      idempotent setup from spec/trial-environment.json; the second
                    run must plan no actions
  deadline-reanalyse x2   T-DEADLINE on retained traces: clean baseline PASS, and
                    a repeat for `compare` (same inputs -> same numbers)
  deadline-sim      T-DEADLINE simulating this tree: clean baseline + control
  deadline-cand     T-DEADLINE --as-candidate late160: a known-bad DUT -> FAIL
                    for the deadline reason
  release           T-RELEASE-BOUND (NO VERDICT while #255's manifest is absent)
  play              T-PLAY-DIGITAL (NO VERDICT while #255's held-note checker is absent)
  timeout           T-DEADLINE sim with a 45 s child budget -> NO VERDICT
  cancel            T-DEADLINE sim, SIGTERM after 60 s -> NO VERDICT, no survivors
  altered-receipt   a copy of a PASS bundle with one artifact edited -> REJECTED
  altered-trace     a committed trace truncated in the tree -> NO VERDICT, restored

Writes build/trials/pilot-summary.json. The summary reports; the receipts
are the evidence (`python tools/trial.py check-all build/trials`).
"""
from __future__ import annotations

import argparse
import gzip
import json
import pathlib
import shutil
import signal
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import trial                                                  # noqa: E402

OUT = ROOT / "build" / "trials"
LOGS = OUT / "pilot-logs"


def receipt_from(log: pathlib.Path) -> str | None:
    for line in reversed(log.read_text().splitlines()):
        if line.startswith("receipt: "):
            return line[len("receipt: "):].strip()
    return None


def start(py: str, name: str, args: list[str]) -> tuple[subprocess.Popen, pathlib.Path]:
    log = LOGS / f"{name}.log"
    fh = open(log, "w")
    p = subprocess.Popen([py, "tools/trial.py", *args], cwd=ROOT, stdout=fh,
                         stderr=subprocess.STDOUT)
    return p, log


def verdict_of(path: str | None) -> dict:
    if not path:
        return {"receipt": None, "verdict": None}
    ok, problems, rec = trial.check_receipt(pathlib.Path(path))
    return {"receipt": str(pathlib.Path(path).relative_to(ROOT)), "valid": ok,
            "problems": problems, "verdict": rec and rec["verdict"],
            "execution": rec and rec["execution"]["status"],
            "reasons": rec and rec["verdict_reasons"][:4]}


def survivors(run_dir: str) -> list[str]:
    r = subprocess.run(["ps", "-eo", "pid,args"], capture_output=True, text=True)
    return [ln for ln in r.stdout.splitlines() if run_dir in ln and "ps -eo" not in ln]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--venv", type=pathlib.Path, required=True)
    a = ap.parse_args(argv)
    LOGS.mkdir(parents=True, exist_ok=True)
    venv = a.venv.expanduser()
    py = str(venv / "bin" / "python")
    summary: dict = {"head": trial.provenance.git("rev-parse", "HEAD").strip()}

    # 1. bootstrap, twice: the second must be a no-op
    boots = []
    for i in (1, 2):
        t0 = time.time()
        r = subprocess.run([sys.executable, "tools/trial_env.py", "bootstrap", "--venv", str(venv)],
                           cwd=ROOT, capture_output=True, text=True)
        (LOGS / f"bootstrap{i}.log").write_text(r.stdout + r.stderr)
        actions = next((ln.split("actions: ", 1)[1] for ln in r.stdout.splitlines()
                        if "actions: " in ln), None)
        boots.append({"rc": r.returncode, "actions": actions, "secs": round(time.time() - t0, 1)})
    summary["bootstrap"] = boots
    if boots[-1]["rc"] != 0:
        summary["refused"] = "bootstrap did not satisfy the spec; no trial run"
        (OUT / "pilot-summary.json").write_text(json.dumps(summary, indent=1))
        print(json.dumps(summary, indent=1))
        return 2

    # 2. the independent trials, together
    jobs = {
        "deadline-reanalyse-1": ["run", "T-DEADLINE", "--mode", "reanalyse"],
        "deadline-reanalyse-2": ["run", "T-DEADLINE", "--mode", "reanalyse"],
        "deadline-sim": ["run", "T-DEADLINE", "--mode", "sim"],
        "deadline-cand-late160": ["run", "T-DEADLINE", "--mode", "sim", "--as-candidate", "late160"],
        "release": ["run", "T-RELEASE-BOUND"],
        "play": ["run", "T-PLAY-DIGITAL"],
        "deadline-timeout45": ["run", "T-DEADLINE", "--mode", "sim", "--timeout", "45"],
    }
    procs = {k: start(py, k, v) for k, v in jobs.items()}

    # 3. cancellation, alongside
    cp, clog = start(py, "deadline-cancel", ["run", "T-DEADLINE", "--mode", "sim"])
    time.sleep(60)
    cp.send_signal(signal.SIGTERM)
    t0 = time.time()
    crc = cp.wait(timeout=120)
    cancel_secs = round(time.time() - t0, 1)
    cancel_receipt = receipt_from(clog)
    left = survivors(str(pathlib.Path(cancel_receipt).parent)) if cancel_receipt else ["no receipt"]
    summary["cancel"] = dict(verdict_of(cancel_receipt), rc=crc, secs_to_exit=cancel_secs,
                             surviving_processes=left)

    for k, (p, log) in procs.items():
        rc = p.wait()
        summary[k] = dict(verdict_of(receipt_from(log)), rc=rc)

    # 4. altered evidence: a published bundle with one artifact edited
    src = summary["deadline-reanalyse-1"]["receipt"]
    if src:
        run_dir = (ROOT / src).parent
        copy = OUT / "altered-bundle" / run_dir.name
        shutil.rmtree(copy, ignore_errors=True)
        shutil.copytree(run_dir, copy)
        rec = copy / "arty-uart-retained" / "record.json"
        data = json.loads(rec.read_text())
        data["facts"]["wire_mismatch"] = 1
        rec.write_text(json.dumps(data))
        ok, problems, _ = trial.check_receipt(copy / "receipt.json")
        summary["altered-receipt"] = {"bundle": str(copy.relative_to(ROOT)), "valid": ok,
                                      "problems": problems}

    # 5. altered retained trace, in the tree, then restored from git
    trace = ROOT / "docs/deadline/traces/prod-arty-uart/uart_i2s.txt.gz"
    original = trace.read_bytes()
    try:
        data = gzip.decompress(original)
        trace.write_bytes(gzip.compress(data[: len(data) // 2], mtime=0))
        p, log = start(py, "deadline-altered-trace", ["run", "T-DEADLINE", "--mode", "reanalyse"])
        rc = p.wait()
        summary["altered-trace"] = dict(verdict_of(receipt_from(log)), rc=rc)
    finally:
        trace.write_bytes(original)
    summary["altered-trace"]["restored"] = subprocess.run(
        ["git", "diff", "--quiet", "--", str(trace)], cwd=ROOT).returncode == 0

    # 6. same inputs, same numbers
    a1, a2 = summary["deadline-reanalyse-1"]["receipt"], summary["deadline-reanalyse-2"]["receipt"]
    if a1 and a2:
        diffs = trial.compare(json.loads((ROOT / a1).read_text()), json.loads((ROOT / a2).read_text()))
        summary["compare-reanalyse"] = {"agree": not diffs, "diffs": diffs}

    (OUT / "pilot-summary.json").write_text(json.dumps(summary, indent=1) + "\n")
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
