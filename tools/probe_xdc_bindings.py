#!/usr/bin/env python3
"""tools/probe_xdc_bindings.py -- #315's assertion on a REAL netlist (read-only).

    python tools/probe_xdc_bindings.py --dcp R1_ROUTED.dcp --dcp-sha256 0f81026e... --out DIR

Opens an existing routed checkpoint in Vivado (no synthesis, no place, no
route, no write) and runs fpga/xdc_bindings.tcl_assertions twice. Each run is
checked against the netlist the R1 image was actually built from:

  fixed     this tree's XDC (g_uart\\.u_uart): every query must bind exactly its
            objects -> exit 0, report END 0
  control   R1's XDC (the pre-#315 g_uart/u_uart pattern, from git at R1's
            freeze): the two UART lines match nothing -> the assertion must
            REFUSE (exit 3), naming XDC lines 42 and 46

The checkpoint digest is asserted before Vivado opens it and after it exits.
Exit 0 when both behave as stated, 1 otherwise, 2 when the apparatus cannot run.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "fpga"))
import xdc_bindings as xb                                  # noqa: E402

R1_FREEZE = "6864435aa6eb"


def sha(p) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def run(vivado, dcp, xdc_text, d: Path) -> dict:
    d.mkdir(parents=True, exist_ok=True)
    rpt = d / xb.REPORT
    tcl = d / "probe.tcl"
    tcl.write_text(f"open_checkpoint {{{dcp}}}\n" + xb.tcl_assertions(xdc_text, str(rpt))
                   + "\nexit 0\n")
    r = subprocess.run([vivado, "-mode", "batch", "-nojournal", "-source", str(tcl),
                        "-log", str(d / "vivado.log")], cwd=d, capture_output=True, text=True,
                       timeout=3600)
    (d / "stdout.txt").write_text(r.stdout + r.stderr)
    refused = [ln for ln in r.stdout.splitlines() if ln.startswith("CONSTRAINT_MATCH_REFUSED")]
    return {"rc": r.returncode, "refused": refused,
            "report": rpt.read_text() if rpt.exists() else None,
            "check_report": xb.check_report(rpt.read_text(), xdc_text) if rpt.exists() else None}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dcp", type=Path, required=True)
    ap.add_argument("--dcp-sha256", required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--vivado", default="vivado")
    a = ap.parse_args(argv)
    vivado = shutil.which(a.vivado)
    if not vivado or not a.dcp.is_file() or sha(a.dcp) != a.dcp_sha256:
        print("probe_xdc_bindings: REFUSED -- no Vivado, or the checkpoint is not the named one")
        return 2
    r1_xdc = subprocess.run(["git", "-C", str(ROOT), "show",
                             f"{R1_FREEZE}:fpga/boards/arty-a7-100.xdc"],
                            capture_output=True, text=True, check=True).stdout
    fixed = run(vivado, a.dcp.resolve(), xb.XDC.read_text(), a.out / "fixed")
    control = run(vivado, a.dcp.resolve(), r1_xdc, a.out / "control-r1-xdc")
    if sha(a.dcp) != a.dcp_sha256:
        print("probe_xdc_bindings: REFUSED -- the checkpoint changed during the probe")
        return 2
    ok_fixed = fixed["rc"] == 0 and fixed["check_report"] == [] and not fixed["refused"]
    ok_ctl = control["rc"] == 3 and any("line 42" in x for x in control["refused"]) \
        and any("line 46" in x for x in control["refused"])
    rec = {"dcp_sha256": a.dcp_sha256, "fixed": fixed, "control_r1_xdc": control,
           "fixed_ok": ok_fixed, "control_caught": ok_ctl}
    (a.out / "probe.json").write_text(json.dumps(rec, indent=1) + "\n")
    print(f"probe_xdc_bindings[fixed]: exit {fixed['rc']}, problems {fixed['check_report']}")
    print("  " + (fixed["report"] or "").replace("\n", "\n  "))
    print(f"probe_xdc_bindings[control R1 XDC]: exit {control['rc']}")
    print("  " + "\n  ".join(control["refused"]))
    print("probe_xdc_bindings: " + ("PASS" if ok_fixed and ok_ctl else "FAIL"))
    return 0 if ok_fixed and ok_ctl else 1


if __name__ == "__main__":
    raise SystemExit(main())
