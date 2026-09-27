#!/usr/bin/env python3
"""fpga/xdc_bindings.py -- every XDC object query must bind the objects it names (#315).

A constraint whose object query matches nothing is DROPPED by Vivado with a
CRITICAL WARNING, and the build still succeeds. That is how the Arty XDC's two
UART-RX synchroniser constraints (lines 42 and 46) shipped dead in R0 and R1:
`.*g_uart/u_uart/...` never matched `u_synth/g_uart.u_uart` (a generate block
is joined with a dot). The text-only gate (ext_io_timing.uart_gate_drift) saw
the lines and passed.

This module turns "the constraint is in the file" into "the constraint bound
exactly the objects it is for":

  * object_queries(xdc)   every `get_cells|get_pins -hier -regexp {...}` in
                          the XDC, with its line and its REQUIRED match count
                          (EXPECT, keyed by the instance it names). A query
                          with no stated count is refused, so a new
                          constraint cannot be added unchecked;
  * tcl_assertions(...)   Tcl for the build, run on the synthesized netlist
                          right after synth_design. It writes one MATCH line
                          per query to constraint_matches.rpt, also checks
                          ASYNC_REG on every matched cell, and EXITS 3
                          (CONSTRAINT_MATCH_REFUSED) on any mismatch, so a
                          dead constraint fails the build rather than the
                          bitstream;
  * check_report(...)     the publisher's check: the shipped report names
                          every query of the compiled XDC, with the required
                          count and ASYNC_REG set. Anything else is refused.

    .venv/bin/python fpga/xdc_bindings.py              # list the queries and counts
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

XDC = Path(__file__).resolve().parent / "boards/arty-a7-100.xdc"
REPORT = "constraint_matches.rpt"

# the instance a query names -> (kind, required count). One line per
# synchroniser: two stages of the UART RX flop, three SPI inputs x two stages;
# each false path goes to exactly one first-stage D pin.
EXPECT = {
    ("u_uart", "cells"): 2,          # rx_q_reg[0..1]
    ("u_uart", "pins"): 1,           # rx_q_reg[0]/D
    ("u_spi", "cells"): 6,           # sck_q, mosi_q, csn_q _reg[0..1]
    ("u_spi", "pins"): 1,            # one first stage per false path
}

_QUERY = re.compile(r"get_(cells|pins)\s+-hier\s+-regexp\s+\{([^}]*)\}")


class Refused(ValueError):
    pass


def object_queries(xdc_text: str) -> list:
    """[(line, kind, regexp, expected, async_reg)] for every object query."""
    out = []
    for n, line in enumerate(xdc_text.splitlines(), 1):
        code = line.split("#", 1)[0]
        for kind, rx in _QUERY.findall(code):
            inst = next((i for i, k in EXPECT if k == kind and i in rx), None)
            if inst is None:
                raise Refused(f"XDC line {n}: {kind} query {{{rx}}} has no required match "
                              "count in fpga/xdc_bindings.EXPECT -- state it before adding "
                              "the constraint")
            out.append((n, kind, rx, EXPECT[(inst, kind)],
                        kind == "cells" and "ASYNC_REG" in code))
    return out


def tcl_assertions(xdc_text: str, report_path: str) -> str:
    """Tcl run after synth_design: count each query, check ASYNC_REG, write the
    report, exit 3 on any mismatch."""
    lines = [f"set cm_fh [open {{{report_path}}} w]", "set cm_bad 0"]
    for n, kind, rx, want, async_reg in object_queries(xdc_text):
        getter = "get_cells" if kind == "cells" else "get_pins"
        lines.append(f"set cm_objs [{getter} -quiet -hier -regexp {{{rx}}}]")
        lines.append("set cm_n [llength $cm_objs]")
        if async_reg:
            lines.append("set cm_ar 0; foreach c $cm_objs "
                         "{ if {[get_property ASYNC_REG $c] == 1} { incr cm_ar } }")
        else:
            lines.append("set cm_ar -")
        lines.append(f'puts $cm_fh "MATCH\\t{n}\\t{kind}\\t$cm_n\\t{want}\\t$cm_ar\\t{rx}"')
        cond = f"$cm_n != {want}" + (f" || $cm_ar != {want}" if async_reg else "")
        lines.append(f"if {{{cond}}} {{ incr cm_bad; "
                     f'puts "CONSTRAINT_MATCH_REFUSED line {n}: {kind} {{{rx}}} matched '
                     f'$cm_n, required {want} (ASYNC_REG $cm_ar)" }}')
    lines += ['puts $cm_fh "END\\t$cm_bad"', "close $cm_fh",
              'if {$cm_bad} { puts "CONSTRAINT_MATCH_REFUSED: $cm_bad constraint(s) bound '
              'the wrong objects"; exit 3 }']
    return "\n".join(lines)


def check_report(report_text: str, xdc_text: str) -> list:
    """Problems with a build's constraint_matches.rpt against the XDC it
    compiled (empty = every constraint bound exactly its objects)."""
    try:
        want = {(n, kind, rx): (exp, ar) for n, kind, rx, exp, ar in object_queries(xdc_text)}
    except Refused as exc:
        return [str(exc)]
    got, end = {}, None
    for line in report_text.splitlines():
        f = line.split("\t")
        if f[0] == "MATCH" and len(f) == 7:
            got[(int(f[1]), f[2], f[6])] = (int(f[3]), int(f[4]), f[5])
        elif f[0] == "END" and len(f) == 2:
            end = int(f[1])
    problems = []
    if end is None:
        problems.append(f"{REPORT} is incomplete (no END line)")
    elif end:
        problems.append(f"{REPORT} records {end} refused constraint(s)")
    for key, (exp, async_reg) in want.items():
        if key not in got:
            problems.append(f"XDC line {key[0]}: {key[1]} {{{key[2]}}} not in {REPORT}")
            continue
        n, exp_rec, ar = got[key]
        if exp_rec != exp:
            problems.append(f"XDC line {key[0]}: the report required {exp_rec}, the XDC "
                            f"requires {exp}")
        if n != exp:
            problems.append(f"XDC line {key[0]}: {key[1]} {{{key[2]}}} matched {n} "
                            f"object(s), required {exp}")
        if async_reg and ar != str(exp):
            problems.append(f"XDC line {key[0]}: ASYNC_REG set on {ar} of {exp} cells")
    for key in set(got) - set(want):
        problems.append(f"{REPORT} names a query the XDC does not hold: line {key[0]}")
    return problems


def main(argv=None) -> int:
    text = XDC.read_text()
    try:
        for n, kind, rx, exp, ar in object_queries(text):
            print(f"line {n}: {kind} {{{rx}}} must match {exp}"
                  + (" (ASYNC_REG on each)" if ar else ""))
    except Refused as exc:
        print(f"xdc_bindings: REFUSED -- {exc}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
