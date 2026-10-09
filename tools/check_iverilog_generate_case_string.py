#!/usr/bin/env python3
"""Guard for the Icarus `generate case` over a string parameter defect (#608).

Runs the committed minimal repro (dsp-dpreg-evidence/dsim/micro.v) under
-g2005 and -g2012 and reports one of:

  KNOWN_DEFECT  -g2005 gives 5a, -g2012 gives xx   (the documented defect;
                the -g2005 workaround is still needed)  exit 0
  FIXED         both give 5a                       (defect gone; the workaround
                and docs/upstream report can be retired)  exit 0
  REFUSED       the apparatus is not in a state to answer.  exit 2
                Every refusal carries a stable reason code (REASONS below).
  UNEXPECTED    -g2012 gives something that is neither 5a nor xx   exit 1

The -g2005 run is the control: if it does not give 5a the repro is broken
and a "-g2012 = xx" reading would be data from a wrong instrument.

Controls: --inject NO_IVERILOG | BROKEN_REPRO | GARBAGE, run with
--expect refused. A control counts as CAUGHT only when it refuses with its
*intended* reason code (CONTROL_MAP). BROKEN_REPRO and GARBAGE additionally
require a clean baseline run (KNOWN_DEFECT or FIXED) on the unmodified repro
first: a refusal from a missing tool or an unrelated compile failure is not
evidence that the mutant was exercised, so it yields NO_VERDICT (exit 2), never
a caught control (verification rule 5).

What satisfies this check while violating its intent: a mutant whose own text
fails to compile would refuse with COMPILE_FAILED; that is rejected because it
is not the intended reason. Tested in test_check_iverilog_generate_case_string.
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable

REPO = Path(__file__).resolve().parent.parent
MICRO = REPO / "fpga/reports/arty/vivado-2025.1/dsp-dpreg-evidence/dsim/micro.v"
EXPECT = "5a"
OUT_RE = re.compile(r"q=([0-9a-fxz]+) \(expect 5a\)")

# Stable refusal reason codes.
NO_TOOL = "NO_TOOL"                      # iverilog or vvp not on PATH
NO_VERSION = "NO_VERSION"                # iverilog -V unreadable
COMPILE_FAILED = "COMPILE_FAILED"        # iverilog exited nonzero
RUN_FAILED = "RUN_FAILED"                # vvp exited nonzero
TIMEOUT = "TIMEOUT"                      # a tool call timed out
OUTPUT_UNPARSEABLE = "OUTPUT_UNPARSEABLE"  # vvp ran, no q=.. line
CONTROL_NOT_EXPECTED = "CONTROL_NOT_EXPECTED"  # -g2005 control is not 5a
REASONS = (NO_TOOL, NO_VERSION, COMPILE_FAILED, RUN_FAILED, TIMEOUT,
           OUTPUT_UNPARSEABLE, CONTROL_NOT_EXPECTED)


class Refusal(Exception):
    def __init__(self, reason: str, detail: str):
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


def run_one(iverilog: str, vvp: str, src: Path, gen: str, tmp: Path) -> str:
    """Return the printed q value, or raise Refusal."""
    out = tmp / f"m{gen}.vvp"
    try:
        r = subprocess.run([iverilog, f"-g{gen}", "-s", "tb", "-o", str(out), str(src)],
                           capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired as e:
        raise Refusal(TIMEOUT, f"-g{gen} compile timed out: {e}") from e
    if r.returncode != 0:
        raise Refusal(COMPILE_FAILED, f"-g{gen} compile failed: {r.stderr.strip()[:200]}")
    try:
        r = subprocess.run([vvp, str(out)], capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired as e:
        raise Refusal(TIMEOUT, f"-g{gen} vvp timed out: {e}") from e
    if r.returncode != 0:
        raise Refusal(RUN_FAILED, f"-g{gen} vvp exit {r.returncode}: {r.stderr.strip()[:200]}")
    m = OUT_RE.search(r.stdout)
    if not m:
        raise Refusal(OUTPUT_UNPARSEABLE, f"-g{gen} output unparseable: {r.stdout.strip()[:200]!r}")
    return m.group(1)


def classify(iverilog: str | None, src: Path,
             vvp: str | None = "vvp") -> tuple[str, str, str]:
    """Return (state, reason, detail). reason is "" unless state is REFUSED."""
    missing = [n for n in (iverilog, vvp) if not n or not shutil.which(n)]
    if missing:
        return "REFUSED", NO_TOOL, f"not found: {missing}"
    assert iverilog and vvp
    try:
        ver = subprocess.run([iverilog, "-V"], capture_output=True, text=True,
                             timeout=30).stdout.splitlines()[0]
    except Exception as e:  # noqa: BLE001 - any failure here means no answer
        return "REFUSED", NO_VERSION, f"cannot read iverilog version: {e}"
    with tempfile.TemporaryDirectory() as t:
        try:
            q05 = run_one(iverilog, vvp, src, "2005", Path(t))
            q12 = run_one(iverilog, vvp, src, "2012", Path(t))
        except Refusal as e:
            return "REFUSED", e.reason, e.detail
    detail = f"{ver}; -g2005 q={q05}, -g2012 q={q12}"
    if q05 != EXPECT:
        return "REFUSED", CONTROL_NOT_EXPECTED, f"-g2005 control is not {EXPECT} ({detail})"
    if q12 == EXPECT:
        return "FIXED", "", detail
    if set(q12) == {"x"}:
        return "KNOWN_DEFECT", "", detail
    return "UNEXPECTED", "", detail


def _broken_repro(text: str) -> str:
    # DIRECT branch removed: both standards give xx, so the control fails
    return text.replace('"DIRECT"  :', '"NOPE"    :')


def _garbage(text: str) -> str:
    return text.replace("q=%h (expect 5a)", "nothing")


# Source mutants. Module-level so tests can substitute a defeating mutant.
MUTANTS: dict[str, Callable[[str], str]] = {
    "BROKEN_REPRO": _broken_repro,
    "GARBAGE": _garbage,
}
# The one refusal reason that proves each control's defect executed.
CONTROL_MAP = {
    "NO_IVERILOG": NO_TOOL,
    "BROKEN_REPRO": CONTROL_NOT_EXPECTED,
    "GARBAGE": OUTPUT_UNPARSEABLE,
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--inject", choices=sorted(CONTROL_MAP))
    ap.add_argument("--expect", choices=["refused"],
                    help="control mode: pass only on REFUSED with the inject's intended reason")
    ap.add_argument("--iverilog", default="iverilog", help=argparse.SUPPRESS)
    ap.add_argument("--vvp", default="vvp", help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    if a.expect == "refused" and not a.inject:
        ap.error("--expect refused requires --inject")
    iverilog: str | None = a.iverilog
    src = MICRO

    if a.expect == "refused" and a.inject in MUTANTS:
        # Precondition: the apparatus must answer on the clean repro, else a
        # refusal on the mutant says nothing about the mutant.
        bstate, breason, bdetail = classify(iverilog, MICRO, a.vvp)
        if bstate not in ("KNOWN_DEFECT", "FIXED"):
            print(f"NO_VERDICT: baseline is {bstate}"
                  f"{'/' + breason if breason else ''} ({bdetail}); "
                  f"control {a.inject} not exercised")
            return 2

    with tempfile.TemporaryDirectory() as t:
        if a.inject == "NO_IVERILOG":
            iverilog = "iverilog-does-not-exist"
        elif a.inject in MUTANTS:
            src = Path(t) / f"{a.inject.lower()}.v"
            src.write_text(MUTANTS[a.inject](MICRO.read_text()))
        state, reason, detail = classify(iverilog, src, a.vvp)
    tag = f"{state}/{reason}" if reason else state
    print(f"{tag}: {detail}")
    if a.expect == "refused":
        want = CONTROL_MAP[a.inject]
        if state == "REFUSED" and reason == want:
            print(f"CAUGHT: {a.inject} refused with intended reason {want}")
            return 0
        print(f"MISSED: {a.inject} expected REFUSED/{want}, got {tag}")
        return 1
    return {"KNOWN_DEFECT": 0, "FIXED": 0, "REFUSED": 2}.get(state, 1)


if __name__ == "__main__":
    sys.exit(main())
