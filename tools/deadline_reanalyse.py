#!/usr/bin/env python3
"""Re-judge every RETAINED deadline capture under the current analysis, with no
simulation, and archive the traces that the judgement rests on.

    .venv/bin/python tools/deadline_reanalyse.py

For each run record in docs/deadline/runs/ (the simulator runs of the batch
at docs/deadline/runs/HEAD.txt), the capture it summarises is found in
rtl-sketch/build/deadline/<tag>/ and:

  1. its IDENTITY is established: the stimulus re-derived from the record's
     scenario must equal the capture's own command file byte for byte, and
     the re-computed schedule summary (frames, missed, worst slacks) must equal
     what the original record states. A capture that fails either is reported
     UNIDENTIFIED and is not used;
  2. it is re-judged by rtl-sketch/verify_deadline.py --analyse-capture
     (analysis version recorded apart from the run's provenance), in one
     tools/run_all.py batch, each job's own exit status the verdict;
  3. its trace files are archived gzip'd under docs/deadline/traces/<record>/
     with their sha256, so the analysis can be repeated without the build tree.

The cost-model totals across all identified deadline-meeting traces are
written to docs/deadline/reanalysis/summary.json.
"""
from __future__ import annotations

import glob
import gzip
import hashlib
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
RUNS = os.path.join(ROOT, "docs", "deadline", "runs")
OUT = os.path.join(ROOT, "docs", "deadline", "reanalysis")
TRACES = os.path.join(ROOT, "docs", "deadline", "traces")
CAPT = os.path.join(ROOT, "rtl-sketch", "build", "deadline")


def tag_of(rec):
    t = rec["scenario"] + ("-p2x" if rec["configuration"]["PULSE2X"] else "")
    if rec.get("inject"):
        t += f"-{rec['inject']}"
    if rec.get("mutant"):
        t += "-" + rec["mutant"].replace(":", "")
    return t


def expect_fail(name):
    return name in ("ctl-prod-late160", "ctl-arty-late160", "ctl-prod-stress-late15")


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    jobs, plan = [], []
    for f in sorted(glob.glob(os.path.join(RUNS, "*-*.json"))):
        name = os.path.basename(f)[:-5]
        if name.startswith("run_all"):
            continue
        rec = json.load(open(f))
        d = os.path.join(CAPT, tag_of(rec))
        if not os.path.isdir(d):
            plan.append(dict(record=name, capture=None, identified=False, why="capture directory absent"))
            continue
        out = os.path.join(OUT, name + ".json")
        cmd = (f"{PY} rtl-sketch/verify_deadline.py --analyse-capture {os.path.relpath(d, ROOT)} "
               f"--record {os.path.relpath(f, ROOT)} --json {os.path.relpath(out, ROOT)}"
               + (" --expect-fail" if expect_fail(name) else ""))
        jobs.append(cmd)
        plan.append(dict(record=name, capture=os.path.relpath(d, ROOT), reanalysis=os.path.relpath(out, ROOT),
                         expect_fail=expect_fail(name)))
    rc = subprocess.run([PY, "tools/run_all.py", "--timeout", "1800", "--tail", "6",
                         "--json", os.path.join(OUT, "run_all.json")] + jobs, cwd=ROOT,
                        stdout=open(os.path.join(OUT, "run_all.log"), "w"), stderr=subprocess.STDOUT).returncode

    # identity, archive and totals
    totals = dict(traces=0, checked_frames=0, exceptions=0, constants={}, ywait_min=None, ywait_max=None)
    for p in plan:
        if not p.get("capture"):
            continue
        orig = json.load(open(os.path.join(RUNS, p["record"] + ".json")))
        try:
            new = json.load(open(os.path.join(ROOT, p["reanalysis"])))
        except (OSError, ValueError):
            p.update(identified=False, why="re-analysis wrote no record")
            continue
        keys = ("frames", "missed", "worst_sample_slack", "worst_busy_slack", "worst_drum_slack")
        diff = {k: (orig["schedule"].get(k), new["schedule"].get(k)) for k in keys
                if orig["schedule"].get(k) != new["schedule"].get(k)}
        p.update(identified=not diff, identity_diff=diff, status=new["status"],
                 original_status=orig["status"], evidence_problems=new.get("evidence_problems"),
                 cost_model=new["schedule"].get("cost_model"),
                 capture_sha256=new.get("reanalysis", {}).get("capture_sha256"))
        dst = os.path.join(TRACES, p["record"])
        shutil.rmtree(dst, ignore_errors=True)
        os.makedirs(dst)
        for n in (p["capture_sha256"] or {}):
            with open(os.path.join(ROOT, p["capture"], n), "rb") as src, \
                    gzip.GzipFile(os.path.join(dst, n + ".gz"), "wb", mtime=0) as gz:
                shutil.copyfileobj(src, gz)
        cm = p["cost_model"]
        if p["identified"] and not new["schedule"]["missed"] and cm and "late" not in p["record"]:
            totals["traces"] += 1
            totals["checked_frames"] += cm["checked_frames"]
            totals["exceptions"] += cm["exceptions"]
            for c, k in cm["residuals"].items():
                totals["constants"][c] = totals["constants"].get(c, 0) + k
            lo, hi = cm["ywait_range"]
            totals["ywait_min"] = lo if totals["ywait_min"] is None else min(lo, totals["ywait_min"])
            totals["ywait_max"] = hi if totals["ywait_max"] is None else max(hi, totals["ywait_max"])
    head = open(os.path.join(RUNS, "HEAD.txt")).read().strip()
    git = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True)
    json.dump(dict(simulator_runs_at=head, analysed_at=git.stdout.strip(),
                   analysis_version=2, run_all_exit=rc, cost_model_totals=totals, captures=plan),
              open(os.path.join(OUT, "summary.json"), "w"), indent=1)
    print(json.dumps(totals))
    for p in plan:
        print(p["record"], p.get("status"), "identified" if p.get("identified") else f"UNIDENTIFIED {p.get('why') or p.get('identity_diff')}",
              (p.get("cost_model") or {}).get("residuals"), (p.get("cost_model") or {}).get("exceptions"))
    return rc


if __name__ == "__main__":
    sys.exit(main())
