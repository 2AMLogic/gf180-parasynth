#!/usr/bin/env python3
"""Guard for the Icarus `generate case` over a string parameter defect (#608).

Runs the committed minimal repro (dsp-dpreg-evidence/dsim/micro.v) under
-g2005 and -g2012 and reports one of:

  KNOWN_DEFECT  -g2005 gives 5a, -g2012 gives xx   (the documented defect;
                the -g2005 workaround is still needed)  exit 0
  FIXED         both give 5a                       (defect gone; the workaround
                and docs/upstream report can be retired)  exit 0
  REFUSED       iverilog missing, output unparseable, or the -g2005 CONTROL is
                not 5a. The apparatus is not in a state to answer.  exit 2
  UNEXPECTED    -g2012 gives something that is neither 5a nor xx   exit 1

The -g2005 run is the control: if it does not give 5a the repro is broken
and a "-g2012 = xx" reading would be data from a wrong instrument.

Controls: --inject NO_IVERILOG | BROKEN_REPRO | GARBAGE  (each must REFUSE).
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MICRO = REPO / "fpga/reports/arty/vivado-2025.1/dsp-dpreg-evidence/dsim/micro.v"
EXPECT = "5a"
OUT_RE = re.compile(r"q=([0-9a-fxz]+) \(expect 5a\)")


def run_one(iverilog: str, src: Path, gen: str, tmp: Path) -> str:
    """Return the printed q value, or raise RuntimeError (-> REFUSED)."""
    vvp = tmp / f"m{gen}.vvp"
    r = subprocess.run([iverilog, f"-g{gen}", "-s", "tb", "-o", str(vvp), str(src)],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f"-g{gen} compile failed: {r.stderr.strip()[:200]}")
    r = subprocess.run(["vvp", str(vvp)], capture_output=True, text=True, timeout=60)
    m = OUT_RE.search(r.stdout)
    if r.returncode != 0 or not m:
        raise RuntimeError(f"-g{gen} output unparseable: {r.stdout.strip()[:200]!r}")
    return m.group(1)


def classify(iverilog: str | None, src: Path, vvp: str | None = None) -> tuple[str, str]:
    if not iverilog or not shutil.which(iverilog) or not (vvp or shutil.which("vvp")):
        return "REFUSED", "iverilog/vvp not found"
    try:
        ver = subprocess.run([iverilog, "-V"], capture_output=True, text=True,
                             timeout=30).stdout.splitlines()[0]
    except Exception as e:  # noqa: BLE001 - any failure here means no answer
        return "REFUSED", f"cannot read iverilog version: {e}"
    with tempfile.TemporaryDirectory() as t:
        try:
            q05 = run_one(iverilog, src, "2005", Path(t))
            q12 = run_one(iverilog, src, "2012", Path(t))
        except (RuntimeError, subprocess.TimeoutExpired) as e:
            return "REFUSED", str(e)
    detail = f"{ver}; -g2005 q={q05}, -g2012 q={q12}"
    if q05 != EXPECT:
        return "REFUSED", f"-g2005 control is not {EXPECT} ({detail})"
    if q12 == EXPECT:
        return "FIXED", detail
    if set(q12) == {"x"}:
        return "KNOWN_DEFECT", detail
    return "UNEXPECTED", detail


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--inject", choices=["NO_IVERILOG", "BROKEN_REPRO", "GARBAGE"])
    ap.add_argument("--expect", choices=["refused"], help="invert: pass only on REFUSED")
    a = ap.parse_args(argv)
    iverilog: str | None = "iverilog"
    src = MICRO
    with tempfile.TemporaryDirectory() as t:
        if a.inject == "NO_IVERILOG":
            iverilog = "iverilog-does-not-exist"
        elif a.inject == "BROKEN_REPRO":
            # DIRECT branch removed: both standards give xx, so the control fails
            src = Path(t) / "broken.v"
            src.write_text(MICRO.read_text().replace('"DIRECT"  :', '"NOPE"    :'))
        elif a.inject == "GARBAGE":
            src = Path(t) / "garbage.v"
            src.write_text(MICRO.read_text().replace("q=%h (expect 5a)", "nothing"))
        state, detail = classify(iverilog, src)
    print(f"{state}: {detail}")
    if a.expect == "refused":
        return 0 if state == "REFUSED" else 1
    return {"KNOWN_DEFECT": 0, "FIXED": 0, "REFUSED": 2}.get(state, 1)


if __name__ == "__main__":
    sys.exit(main())
