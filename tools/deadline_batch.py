#!/usr/bin/env python3
"""Re-run every job behind docs/deadline/README.md in one batch.

    .venv/bin/python tools/deadline_batch.py            # writes docs/deadline/runs/

The production-deadline runs (rtl-sketch/verify_deadline.py), the controls
that must fail for the deadline reason, the candidate correction's
evaluation, and the component bench (rtl-sketch/verify_voice.py) at the
production launch, with its regressions. Every verdict is tools/run_all.py's
record of each job's own exit status; nothing here reads text for a verdict.
"""
from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
RUNS = os.path.join(ROOT, "docs", "deadline", "runs")
MUT = os.path.join(ROOT, "build", "dl", "mut")


def jobs() -> list[str]:
    d = f"{PY} rtl-sketch/verify_deadline.py"
    v = f"{PY} rtl-sketch/verify_voice.py"
    r = "docs/deadline/runs"
    m = "build/dl/mut"
    return [
        # production: the refused configuration, the stress and the register-legal extremes
        f"{d} --scenario threesaw-f1cal --json {r}/prod-threesaw-f1cal.json",
        f"{d} --scenario threesaw-f1cal --pulse2x --json {r}/prod-threesaw-f1cal-p2x.json",
        f"{d} --scenario stress-saw --json {r}/prod-stress-saw.json",
        f"{d} --scenario stress-saw --pulse2x --json {r}/prod-stress-saw-p2x.json",
        f"{d} --scenario stress-pulse --pulse2x --json {r}/prod-stress-pulse-p2x.json",
        f"{d} --scenario extreme-saw --json {r}/prod-extreme-saw.json",
        f"{d} --scenario extreme-saw-mod --json {r}/prod-extreme-saw-mod.json",
        f"{d} --scenario extreme-pulse --pulse2x --json {r}/prod-extreme-pulse-p2x.json",
        f"{d} --scenario extreme-pulse-mod --pulse2x --json {r}/prod-extreme-pulse-mod-p2x.json",
        f"{d} --scenario arty-uart --json {r}/prod-arty-uart.json",
        # late-completion controls: rejected for the DEADLINE reason, and the 1-cycle boundary
        f"{d} --scenario threesaw-f1cal --mutant late:160 --expect-fail --json {r}/ctl-prod-late160.json",
        f"{d} --scenario arty-uart --mutant late:160 --expect-fail --json {r}/ctl-arty-late160.json",
        f"{d} --scenario stress-saw --mutant late:14 --json {r}/ctl-prod-stress-late14.json",
        f"{d} --scenario stress-saw --mutant late:15 --expect-fail --json {r}/ctl-prod-stress-late15.json",
        # the candidate correction, evaluated (not shipped)
        f"{d} --scenario extreme-pulse --pulse2x --mutant skip2xwin --json {r}/cand-extreme-pulse-p2x-skip2xwin.json",
        f"{d} --scenario extreme-pulse-mod --pulse2x --mutant skip2xwin --json {r}/cand-extreme-pulse-mod-p2x-skip2xwin.json",
        f"{d} --scenario stress-pulse --pulse2x --mutant skip2xwin --json {r}/cand-stress-pulse-p2x-skip2xwin.json",
        f"{d} --scenario extreme-saw-mod --mutant skip2xwin --json {r}/cand-extreme-saw-mod-skip2xwin.json",
        # the component bench at the production launch
        f"{v} --set quick --only threesaw --filter2x --outdir build/dl/tbv-threesaw",
        f"{v} --set quick --only threesaw --filter2x --pulse2x --outdir build/dl/tbv-threesaw-p2x",
        f"{v} --set quick --only threesaw --filter2x --go 48 --expect-deadline-fail --outdir build/dl/tbv-threesaw-go48",
        f"{v} --set quick --only threesaw --filter2x --rtl {m}/mutant-late160/voice_dp.v --expect-deadline-fail --outdir build/dl/tbv-late160",
        f"{v} --set quick --only threesaw --filter2x --rtl {m}/mutant-late37/voice_dp.v --outdir build/dl/tbv-late37",
        f"{v} --set quick --only threesaw --filter2x --rtl {m}/mutant-late38/voice_dp.v --expect-deadline-fail --outdir build/dl/tbv-late38",
        f"{v} --set quick --only f1cal --filter2x --outdir build/dl/tbv-f1cal",
        f"{v} --set quick --outdir build/dl/tbv-quick",
        f"{v} --set quick --osc2x --outdir build/dl/tbv-quick-osc2x",
        f"{v} --set quick --only waves3 --filter2x --outdir build/dl/tbv-w3-f2x",
        f"{v} --set quick --only waves3 --filter2x --pulse2x --outdir build/dl/tbv-w3-p2x",
        f"{v} --set quick --only default --osc2x --inject OSC2X_OFF --expect-fail --outdir build/dl/tbv-osc2x-off",
        f"{v} --set quick --only default --rtl rtl-sketch/stubs/voice_dp_stub.v --expect-fail --outdir build/dl/tbv-stub",
        f"{PY} -m pytest tools/test_verify_deadline.py fpga/test_build_arty.py fpga/test_publish_arty.py "
        f"fpga/test_build_selected.py fpga/test_publish_selected.py -q",
    ]


def main() -> int:
    os.makedirs(RUNS, exist_ok=True)
    for spec in ("late:160", "late:37", "late:38"):
        r = subprocess.run([PY, "rtl-sketch/verify_deadline.py", "--write-mutant", spec,
                            "--outdir", MUT], cwd=ROOT, capture_output=True, text=True)
        if r.returncode != 0:
            print(f"deadline_batch: REFUSED -- mutant {spec}: {r.stdout}{r.stderr}")
            return 2
    head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                          capture_output=True, text=True).stdout.strip()
    with open(os.path.join(RUNS, "HEAD.txt"), "w") as fh:
        fh.write(head + "\n")
    cmd = [PY, "tools/run_all.py", "--timeout", "5400", "--tail", "18",
           "--json", os.path.join(RUNS, "run_all.json")] + jobs()
    with open(os.path.join(RUNS, "run_all.log"), "w") as log:
        return subprocess.run(cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT).returncode


if __name__ == "__main__":
    sys.exit(main())
