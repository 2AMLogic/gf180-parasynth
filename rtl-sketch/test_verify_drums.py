#!/usr/bin/env python3
"""`verify_drums.py` must resolve --outdir absolutely before iverilog runs.

    .venv/bin/python -m pytest rtl-sketch/test_verify_drums.py -q

simulate() runs iverilog with cwd=rtl-sketch (so the DUT's $readmemh files
resolve from there), while generate() writes its stimulus relative to the
process CWD. A relative --outdir therefore split one directory into two:
stimulus under <cwd>/build/drum-clean, the .vvp attempted at
rtl-sketch/build/drum-clean/tb_drums.vvp -- ENOENT, iverilog "Code generator
failure: -1", and every drums control NO-VERDICT. `make controls` had never
been run end to end, so nothing had ever seen it: in the #1126 close-out runs
the drums baseline refused this way twice, deterministically, while a solo run
with an absolute --outdir passed -- the apparatus state, not the load.

The sibling verifiers normalise in main (verify_ctl.py:
`a.outdir = os.path.abspath(a.outdir)`); this pins the same for drums. No
iverilog needed: subprocess.run is recorded and faked, and the recorded argv
is the defect.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

HERE = str(Path(__file__).resolve().parent)
sys.path.insert(0, HERE)

import verify_drums                                                   # noqa: E402


def test_a_relative_outdir_reaches_iverilog_absolute(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    calls = []

    def fake_run(cmd, **kw):
        calls.append((list(cmd), kw.get("cwd")))
        if os.path.basename(cmd[0]).startswith("vvp"):
            # the testbench writes the RTL dump; one line is enough for compare
            Path(cmd[cmd.index("-n") + 1]).parent.joinpath("drum_rtl_out.txt").write_text("0\n")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(verify_drums.subprocess, "run", fake_run)
    rc = verify_drums.main(["--short", "--outdir", "reldrum"])  # rc is the comparison's, not under test
    assert calls, "iverilog was never invoked"
    iverilog = [c for c in calls if os.path.basename(c[0][0]).startswith("iverilog")][0]
    vvp = [c for c in calls if os.path.basename(c[0][0]).startswith("vvp")][0]
    want = os.path.abspath(os.path.join(str(tmp_path), "reldrum"))
    for cmd, cwd in (iverilog, vvp):
        target = cmd[cmd.index("-o") + 1] if "-o" in cmd else cmd[cmd.index("-n") + 1]
        assert os.path.isabs(target), (
            f"{target!r} is relative while iverilog/vvp run with cwd={cwd!r}: "
            "a relative --outdir makes the .vvp path resolve under rtl-sketch, "
            "not under the outdir the stimulus was written to")
        assert os.path.normpath(os.path.dirname(target)) == want
    assert isinstance(rc, int)
