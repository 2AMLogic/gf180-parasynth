#!/usr/bin/env python3
"""Re-capture the two `-l2` stress-saw evidence traces against the CURRENT tree.

    .venv/bin/python tools/deadline_recapture_l2.py

WHY THIS EXISTS. `tools/test_verify_deadline.py::_capture` refuses to judge a
committed capture that was not driven by this tree's stimulus -- it compares
the capture's `top_bx_cmds.txt` byte for byte against what
`verify_deadline.SPI_SCENARIOS["stress-saw"]` + `verify_synth_top.write_cmds`
build now. That precondition is correct and must not be relaxed: judging
historical evidence against current source is how a false green happens.

The consequence is that the capture goes stale whenever the stimulus moves,
and it has done so twice inside 24 h (#426, and contract revision 14 before
it). Both times the fix was the same two simulator runs plus a gzip, and both
times the recipe had to be reconstructed by reading a months-old commit's
`--stat` output and its message. That reconstruction is the thing this file
removes. It does NOT change what the capture binds to, which is the actual
cause of the staleness -- that is #443.

WHAT IT DOES, in the order `8c3de22` did it by hand:

  1. runs the clean `stress-saw` capture and its `late:15` negative control as
     one `tools/run_all.py` batch (each job's own exit status is the verdict),
  2. copies the three run records into `docs/deadline/runs/`,
  3. archives the four trace files each run writes, gzip'd with `mtime=0` so
     the bytes are reproducible, into `docs/deadline/traces/<record>/`.

It does NOT run `tools/deadline_reanalyse.py`: that tool maps a record back to
a capture directory by scenario name, so both `prod-stress-saw` and
`prod-stress-saw-l2` resolve to the same `build/deadline/stress-saw`, and it
would overwrite the retained pre-L2 history with the new capture.

Exit status is `tools/run_all.py`'s: 0 only if both simulator jobs exited 0
(the control is run with `--expect-fail`, so 0 means the deadline check caught
it). Nothing is copied or archived when a job fails.
"""
from __future__ import annotations

import gzip
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNS = os.path.join(ROOT, "docs", "deadline", "runs")
TRACES = os.path.join(ROOT, "docs", "deadline", "traces")
WORK = os.path.join(ROOT, "build", "dl")

# The four files the test's _capture reads back. top_samp_*.txt and the .vvp
# the run also writes are build artefacts, not evidence, and stay out.
ARCHIVED = ("top_bx_cmds.txt",
            "top_i2s_VOICE_OSC_2X_VOICE_FILTER_2X.txt",
            "top_wrs_VOICE_OSC_2X_VOICE_FILTER_2X.txt",
            "top_wrs_VOICE_OSC_2X_VOICE_FILTER_2X.txt.sched")

CAPTURES = (
    ("prod-stress-saw-l2", []),
    ("ctl-prod-stress-late15-l2", ["--mutant", "late:15", "--expect-fail"]),
)


def main(argv=None) -> int:
    py = sys.executable
    os.makedirs(WORK, exist_ok=True)
    jobs = []
    for name, extra in CAPTURES:
        jobs.append(" ".join([py, "rtl-sketch/verify_deadline.py", "--scenario", "stress-saw",
                              *extra, "--outdir", f"build/dl/{name}", "--json",
                              f"build/dl/{name}.json"]))
    batch = os.path.join(WORK, "run_all-l2.json")
    log = os.path.join(WORK, "run_all-l2.log")
    with open(log, "w") as fh:
        rc = subprocess.run([py, "tools/run_all.py", "--timeout", "1800",
                             "--json", batch] + jobs,
                            cwd=ROOT, stdout=fh, stderr=subprocess.STDOUT).returncode
    print(open(log).read(), end="")
    if rc != 0:
        print(f"deadline_recapture_l2: REFUSED -- a simulator job did not pass "
              f"(tools/run_all.py exit {rc}); nothing archived, see {log}")
        return rc

    shutil.copyfile(batch, os.path.join(RUNS, "run_all-l2.json"))
    for name, _ in CAPTURES:
        shutil.copyfile(os.path.join(WORK, name + ".json"), os.path.join(RUNS, name + ".json"))
        src = os.path.join(WORK, name)
        dst = os.path.join(TRACES, name)
        shutil.rmtree(dst, ignore_errors=True)
        os.makedirs(dst)
        for n in ARCHIVED:
            with open(os.path.join(src, n), "rb") as f, \
                    gzip.GzipFile(os.path.join(dst, n + ".gz"), "wb", mtime=0) as gz:
                shutil.copyfileobj(f, gz)
        rec = json.load(open(os.path.join(RUNS, name + ".json")))
        print(f"deadline_recapture_l2: {name} {rec['status']} "
              f"({rec['facts']['periods']}/{rec['facts']['periods_required']} periods, "
              f"{rec['facts']['writes_seen']}/{rec['facts']['writes_sent']} writes, "
              f"missed {rec['schedule']['missed']}) -> {len(ARCHIVED)} files archived")
    return 0


if __name__ == "__main__":
    sys.exit(main())
