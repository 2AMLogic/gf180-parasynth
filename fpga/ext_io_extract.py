#!/usr/bin/env python3
"""Per-port external I/O timing of ONE routed Arty checkpoint, read-only.

    python fpga/ext_io_extract.py --dcp build/arty/routed.dcp \
        --dcp-sha256 <digest> --out <fresh dir>

Why this exists (#280). The publisher (fpga/publish_arty.py) derives
`external_io_timing_qualified` from the routed timing.rpt: no output port is
unconstrained or false-pathed, and the design-wide WNS/WHS pass. That report
lists only the WORST path per clock group, so a per-port figure -- the
spi_miso clock-to-out the readback rate rests on, the uart_txd and LED
slacks -- is not in it. The last per-port figures (fpga/reports/arty/
ext-io-checkpoint) were measured on an earlier checkpoint and do not carry to
a new image. This asks the routed checkpoint itself, one port at a time.

REFUSED is a first-class outcome (docs/failure-modes.md), exit 2:
  * the checkpoint's sha256 is asserted BEFORE Vivado opens it and re-hashed
    AFTER it exits (the Tcl has no write_checkpoint/opt/place/route; the
    in-session control below changes constraints in memory only);
  * Vivado is asserted to be 2025.1 inside the Tcl;
  * every expected output port must report a max and a min path;
  * the injected control must be caught (below) or nothing is reported.

THE CONTROL (docs/verification-rules.md rule 2). After the clean
measurement, the same session replaces spi_miso's -max output delay with
80.000 ns (IMPOSSIBLE_MISO: a CO budget of 1.38 ns, which no Artix-7 output
buffer meets). The measurement is accepted only when that control turns
spi_miso's setup slack negative AND leaves every other port's verdict alone
-- a checker that cannot see an impossible budget cannot vouch for a
possible one.

Also recorded (data, not a gate): whether the XDC's synchroniser patterns
match any cell of this netlist, and the ASYNC_REG property of the cells the
RTL actually built. A constraint whose pattern matches nothing is dropped
by Vivado with a CRITICAL WARNING and never reaches the routed design.

Exit: 0 PASS (every timed output meets setup and hold, control caught);
1 FAIL (a timed output fails); 2 REFUSED.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import ext_io_timing as iot  # noqa: E402

# the outputs the XDC gives a real output delay (fpga/boards/arty-a7-100.xdc);
# i2s_bclk is the one permitted exception (a forwarded clock, no output delay)
TIMED_OUTPUTS = ("i2s_lrclk", "i2s_sdata", "spi_miso", "led[1]", "led[2]", "led[3]",
                 "uart_txd")
INPUTS = ("spi_sck", "spi_mosi", "spi_cs_n", "uart_rxd", "btn_reset")
CORE_CLOCK = "hardware_clock.clock_raw"
IMPOSSIBLE_MISO_MAX_NS = 80.000
# the XDC's synchroniser patterns, verbatim, and what each should match
# (R2: the UART pair follows #315's fixed XDC, g_uart\.u_uart; R1's record was
# made with the old patterns and is checked with that instrument, pinned by
# version in fpga/release/r1_release.py)
SYNC_PATTERNS = {
    "xdc:spi_async_reg": (r".*u_spi/(sck_q|mosi_q|csn_q)_reg\[[01]\]", "cells", 6),
    "xdc:spi_false_path_d": (r".*u_spi/(sck_q|mosi_q|csn_q)_reg\[0\]/D", "pins", 3),
    "xdc:uart_async_reg": (r".*g_uart\.u_uart/rx_q_reg\[[01]\]", "cells", 2),
    "xdc:uart_false_path_d": (r".*g_uart\.u_uart/rx_q_reg\[0\]/D", "pins", 1),
    # what the netlist actually holds (any hierarchy separator)
    "netlist:uart_rx_q": (r".*u_uart/rx_q_reg\[[01]\]", "cells", 2),
}

TCL = r"""
if {![string match "2025.1*" [version -short]]} { puts "REFUSED_VERSION [version -short]"; exit 3 }
open_checkpoint {%DCP%}
set fh [open {%OUT%/ext_io_paths.txt} w]
proc measure {fh variant ports} {
  foreach p $ports {
    foreach kind {max min} {
      set tp [get_timing_paths -to [get_ports $p] -delay_type $kind -max_paths 1 -nworst 1]
      if {[llength $tp] == 0} { puts $fh "PATH\t$variant\t$p\t$kind\tNONE\t\t\t\t"; continue }
      puts $fh "PATH\t$variant\t$p\t$kind\t[get_property SLACK $tp]\t[get_property REQUIREMENT $tp]\t[get_property DATAPATH_DELAY $tp]\t[get_property STARTPOINT_CLOCK $tp]\t[get_property ENDPOINT_CLOCK $tp]"
    }
  }
}
set outs {%OUTS%}
measure $fh clean $outs
foreach p {%INS%} {
  set tp [get_timing_paths -from [get_ports $p] -delay_type max -max_paths 1 -nworst 1]
  if {[llength $tp] == 0} { puts $fh "INPUT\t$p\tNONE"; continue }
  puts $fh "INPUT\t$p\t[get_property SLACK $tp]\t[get_property ENDPOINT_PIN $tp]"
}
%SYNC%
set_output_delay -clock [get_clocks {%CORE%}] -max %IMPOSSIBLE% [get_ports spi_miso]
measure $fh IMPOSSIBLE_MISO $outs
close $fh
puts "EXTRACT_DONE"
exit
"""


class Refused(RuntimeError):
    pass


def sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def tcl(dcp: str, out: str) -> str:
    sync = []
    for label, (rx, kind, _want) in SYNC_PATTERNS.items():
        getter = "get_cells" if kind == "cells" else "get_pins"
        sync.append(f"set objs [{getter} -quiet -hier -regexp {{{rx}}}]")
        sync.append(f'puts $fh "SYNC\\t{label}\\t[llength $objs]\\t[join $objs ,]"')
    sync.append(r"foreach c [get_cells -quiet -hier -regexp {.*u_(uart|spi)/(rx_q|sck_q|mosi_q|csn_q)_reg\[[01]\]}] {")
    sync.append(r'  puts $fh "ASYNC\t$c\t[get_property ASYNC_REG $c]"')
    sync.append("}")
    return (TCL.replace("%DCP%", dcp).replace("%OUT%", out)
            .replace("%OUTS%", " ".join("{" + p + "}" for p in TIMED_OUTPUTS))
            .replace("%INS%", " ".join(INPUTS)).replace("%SYNC%", "\n".join(sync))
            .replace("%CORE%", CORE_CLOCK).replace("%IMPOSSIBLE%", f"{IMPOSSIBLE_MISO_MAX_NS:.3f}"))


def _num(s: str):
    try:
        return float(s)
    except ValueError:
        return None


def parse(text: str) -> dict:
    paths, inputs, sync, async_reg = {}, {}, {}, {}
    for line in text.splitlines():
        f = line.split("\t")
        if f[0] == "PATH" and len(f) >= 5:
            rec = None if f[4] == "NONE" else {
                "slack_ns": _num(f[4]), "requirement_ns": _num(f[5]),
                "datapath_delay_ns": _num(f[6]), "launch_clock": f[7], "capture_clock": f[8]}
            paths.setdefault(f[1], {}).setdefault(f[2], {})[f[3]] = rec
        elif f[0] == "INPUT":
            inputs[f[1]] = None if f[2] == "NONE" else {"slack_ns": _num(f[2]),
                                                         "endpoint": f[3] if len(f) > 3 else ""}
        elif f[0] == "SYNC":
            sync[f[1]] = {"matched": int(f[2]), "objects": [o for o in f[3].split(",") if o]}
        elif f[0] == "ASYNC":
            async_reg[f[1]] = f[2]
    return {"paths": paths, "inputs": inputs, "sync": sync, "async_reg": async_reg}


def port_verdicts(variant: dict) -> dict:
    """{port: PASS | FAIL | MISSING} for the timed outputs of one variant."""
    out = {}
    for p in TIMED_OUTPUTS:
        rec = variant.get(p, {})
        slacks = [(rec.get(k) or {}).get("slack_ns") for k in ("max", "min")]
        if any(s is None for s in slacks):
            out[p] = "MISSING"
        else:
            out[p] = "PASS" if min(slacks) >= 0 else "FAIL"
    return out


def evaluate(parsed: dict) -> dict:
    clean = parsed["paths"].get("clean")
    ctl = parsed["paths"].get("IMPOSSIBLE_MISO")
    if not clean or not ctl:
        raise Refused("the Tcl reported no clean or no control measurement")
    v_clean, v_ctl = port_verdicts(clean), port_verdicts(ctl)
    missing = [p for p, v in {**v_clean, **v_ctl}.items() if v == "MISSING"]
    if missing:
        raise Refused(f"no max/min timed path to {sorted(set(missing))}: the port is "
                      "unconstrained or absent in this checkpoint")
    expected_ctl = dict(v_clean, spi_miso="FAIL")
    caught = v_clean["spi_miso"] == "PASS" and v_ctl == expected_ctl
    if not caught:
        raise Refused(f"control IMPOSSIBLE_MISO not caught: clean {v_clean}, control {v_ctl} "
                      f"(expected {expected_ctl}); the measurement cannot vouch for anything")
    miso = clean["spi_miso"]["max"]
    od_max = iot.miso_output_delay_max_ns()
    # the STA-equivalent clock-to-out: what the -max output delay check
    # enforces (CO <= T_core - od_max), less the slack left over. It
    # includes the clock skew/uncertainty STA applied, so it bounds the real
    # clock-to-out from above.
    co = round(iot.T_CORE_NS - od_max - miso["slack_ns"], 3)
    readback = iot.miso_max_readback_mhz(co)
    failing = [p for p, v in v_clean.items() if v != "PASS"]
    return {
        "verdict": "PASS" if not failing else "FAIL",
        "failing_ports": failing,
        "ports": {p: {"verdict": v_clean[p], "setup_slack_ns": clean[p]["max"]["slack_ns"],
                      "hold_slack_ns": clean[p]["min"]["slack_ns"],
                      "capture_clock": clean[p]["max"]["capture_clock"]}
                  for p in TIMED_OUTPUTS},
        "worst_setup_slack_ns": min(clean[p]["max"]["slack_ns"] for p in TIMED_OUTPUTS),
        "worst_hold_slack_ns": min(clean[p]["min"]["slack_ns"] for p in TIMED_OUTPUTS),
        "spi_miso": {"output_delay_max_ns": round(od_max, 3), "setup_slack_ns": miso["slack_ns"],
                     "sta_co_bound_ns": co, "co_budget_ns": round(iot.T_CORE_NS - od_max, 3),
                     "max_guaranteed_readback_mhz": round(readback, 4),
                     "readback_qualified_mhz": iot.READBACK_QUALIFIED_MHZ,
                     "readback_qualified": readback >= iot.READBACK_QUALIFIED_MHZ,
                     "write_ceiling_mhz": iot.SCK_WRITE_MAX_MHZ},
        "exceptions": {"i2s_bclk": "forwarded clock (create_generated_clock); no output delay "
                                   "by design -- publish_arty PERMITTED_OUTPUT_DELAY_EXCEPTIONS"},
        "control": {"id": "IMPOSSIBLE_MISO", "caught": True,
                    "injected": f"set_output_delay -max {IMPOSSIBLE_MISO_MAX_NS:.3f} on spi_miso "
                                "(in memory, after the clean measurement)",
                    "spi_miso_setup_slack_ns": ctl["spi_miso"]["max"]["slack_ns"],
                    "verdicts": v_ctl},
        "inputs": parsed["inputs"],
        "synchronisers": {"patterns": {k: {"pattern": SYNC_PATTERNS[k][0],
                                           "expected": SYNC_PATTERNS[k][2],
                                           **parsed["sync"].get(k, {"matched": None,
                                                                    "objects": []})}
                                       for k in SYNC_PATTERNS},
                          "async_reg": parsed["async_reg"]},
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dcp", type=Path, required=True)
    ap.add_argument("--dcp-sha256", required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--vivado", default="vivado")
    ap.add_argument("--parse-only", type=Path, default=None,
                    help="evaluate an existing ext_io_paths.txt (no Vivado)")
    a = ap.parse_args(argv)
    out = a.out.resolve()
    record = {"schema": "ext-io-extract v1", "state": "REFUSED",
              "dcp": str(a.dcp), "dcp_sha256": a.dcp_sha256,
              "instrument_sha256": sha(Path(__file__))}

    def finish(code: int) -> int:
        out.mkdir(parents=True, exist_ok=True)
        (out / "ext-io-extract.json").write_text(json.dumps(record, indent=1) + "\n")
        print(f"ext_io_extract: {record['state']} -- {record.get('reason', '')}"
              f"{record.get('summary', '')}")
        return code

    try:
        if a.parse_only is None:
            if out.exists() and any(out.iterdir()):
                raise Refused(f"{out} is not empty; extract into a fresh directory")
            out.mkdir(parents=True, exist_ok=True)
            if not a.dcp.is_file() or sha(a.dcp) != a.dcp_sha256:
                raise Refused("checkpoint digest mismatch before Vivado opened it")
            script = out / "ext_io_extract.tcl"
            script.write_text(tcl(str(a.dcp.resolve()), str(out)))
            record["tcl_sha256"] = sha(script)
            r = subprocess.run([a.vivado, "-mode", "batch", "-nojournal", "-source", str(script),
                                "-log", str(out / "vivado_ext_io.log")],
                               cwd=out, capture_output=True, text=True, timeout=3600)
            record["vivado_rc"] = r.returncode
            if sha(a.dcp) != a.dcp_sha256:
                raise Refused("checkpoint changed during extraction")
            if r.returncode != 0 or "EXTRACT_DONE" not in r.stdout:
                raise Refused(f"Vivado batch failed (rc {r.returncode}): {r.stdout[-400:]}")
            log = (out / "vivado_ext_io.log").read_text()
            ver = re.search(r"Vivado v(\S+) \(64-bit\).*?SW Build (\d+)", log, re.S)
            record["tool"] = f"Vivado v{ver[1]} SW Build {ver[2]}" if ver else None
            record["log_sha256"] = sha(out / "vivado_ext_io.log")
            paths = out / "ext_io_paths.txt"
        else:
            paths = a.parse_only
        record["paths_sha256"] = sha(paths)
        result = evaluate(parse(paths.read_text()))
    except (Refused, OSError, subprocess.TimeoutExpired) as exc:
        record["reason"] = str(exc)
        return finish(2)
    record.update(result, state=result["verdict"])
    m = result["spi_miso"]
    record["summary"] = (f"{len(TIMED_OUTPUTS)} timed outputs, worst setup "
                         f"{result['worst_setup_slack_ns']:+.3f} ns, worst hold "
                         f"{result['worst_hold_slack_ns']:+.3f} ns; spi_miso CO <= "
                         f"{m['sta_co_bound_ns']} ns -> readback <= "
                         f"{m['max_guaranteed_readback_mhz']} MHz; control caught")
    return finish(0 if result["verdict"] == "PASS" else 1)


if __name__ == "__main__":
    sys.exit(main())
