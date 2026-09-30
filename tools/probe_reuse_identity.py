#!/usr/bin/env python3
"""tools/probe_reuse_identity.py -- #313 end to end, on the real simulator.

    python tools/probe_reuse_identity.py --out build/reuse-313

Three runs of `fpga/verify_live_midi.py --rtl coverage --reuse-rtl-if-identical`
into ONE output directory, so the second and third runs find the first run's
RTL replay on disk:

  1. fresh      no run on disk: must SIMULATE
  2. identical  same sources, stimulus and simulator: must REUSE
  3. control    PATH puts a wrapper `iverilog` first. It answers `-V` with a
                different version and otherwise runs the real iverilog. Same
                sources and stimulus, so only the simulator identity differs:
                must SIMULATE, naming the simulator as the reason

Each run's rtl_run report (fpga/verify_uart_bridge.rtl_run_report, via the
live-MIDI record) is read from its verification.json. Exit 0 when all three
behave as stated, 1 otherwise, 2 when a run did not produce a record.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FAKE = "Icarus Verilog version 12.0 (stable) (FAKED by tools/probe_reuse_identity.py)"


def run(out: Path, env: dict, label: str) -> dict:
    t0 = time.time()
    r = subprocess.run([sys.executable, str(ROOT / "fpga/verify_live_midi.py"),
                        "--outdir", str(out), "--rtl", "coverage", "--reuse-rtl-if-identical"],
                       cwd=ROOT, env=env, capture_output=True, text=True)
    (out / f"{label}.log").write_text(r.stdout + r.stderr)
    rec = json.loads((out / "verification.json").read_text()) \
        if (out / "verification.json").exists() else {}
    rr = (rec.get("rtl") or {}).get("coverage") or {}
    return {"label": label, "rc": r.returncode, "secs": round(time.time() - t0, 1),
            "state": rr.get("state"), "rtl_run": rr.get("rtl_run")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    out = a.out.resolve()
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    real = shutil.which("iverilog")
    if not real:
        print("probe_reuse_identity: REFUSED -- iverilog not on PATH")
        return 2
    env = dict(os.environ)
    results = [run(out, env, "1-fresh"), run(out, env, "2-identical")]
    fake_bin = out / "fakebin"
    fake_bin.mkdir()
    w = fake_bin / "iverilog"
    w.write_text(f'#!/bin/sh\nif [ "$1" = "-V" ]; then echo "{FAKE}"; exit 0; fi\n'
                 f'exec "{real}" "$@"\n')
    w.chmod(w.stat().st_mode | stat.S_IEXEC)
    results.append(run(out, dict(env, PATH=f"{fake_bin}:{env['PATH']}"), "3-control"))
    want = [False, True, False]
    problems = []
    for r, w_reused in zip(results, want):
        rr = r["rtl_run"]
        if not rr:
            print(f"probe_reuse_identity[{r['label']}]: NO RECORD (rc {r['rc']})")
            return 2
        print(f"probe_reuse_identity[{r['label']}]: rc {r['rc']}, {r['secs']} s, RTL "
              f"{r['state']}, reused {rr['reused']} -- {rr['why']}; simulator {rr['iverilog']!r}")
        if rr["reused"] is not w_reused:
            problems.append(f"{r['label']}: reused {rr['reused']}, expected {w_reused}")
        if r["state"] != "PASS":
            problems.append(f"{r['label']}: RTL coverage {r['state']}")
    ctl = results[2]["rtl_run"]
    if "simulator" not in (ctl.get("why") or "") or ctl.get("iverilog") != FAKE:
        problems.append(f"control: re-simulated for {ctl.get('why')!r} with simulator "
                        f"{ctl.get('iverilog')!r}, not for the faked simulator")
    (out / "summary.json").write_text(json.dumps({"runs": results, "problems": problems},
                                                 indent=1) + "\n")
    print("probe_reuse_identity: " + ("PASS" if not problems else "FAIL -- " + "; ".join(problems)))
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
