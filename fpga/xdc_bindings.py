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

import hashlib
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


def query_id(rx: str) -> str:
    """A report-safe identity of a query's pattern. Tcl substitutes backslashes
    inside a double-quoted puts, so the pattern text itself cannot be written
    to the report verbatim (measured: `\\[` came out as `[`)."""
    return hashlib.sha256(rx.encode()).hexdigest()[:16]


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
        lines.append(f'puts $cm_fh "MATCH\\t{n}\\t{kind}\\t$cm_n\\t{want}\\t$cm_ar\\t{query_id(rx)}"')
        cond = f"$cm_n != {want}" + (f" || $cm_ar != {want}" if async_reg else "")
        lines.append(f"if {{{cond}}} {{ incr cm_bad; "
                     f'puts "CONSTRAINT_MATCH_REFUSED line {n}: {kind} query {query_id(rx)} matched '
                     f'$cm_n, required {want} (ASYNC_REG $cm_ar)" }}')
    lines += ['puts $cm_fh "END\\t$cm_bad"', "close $cm_fh",
              'if {$cm_bad} { puts "CONSTRAINT_MATCH_REFUSED: $cm_bad constraint(s) bound '
              'the wrong objects"; exit 3 }']
    return "\n".join(lines)


def check_report(report_text: str, xdc_text: str) -> list:
    """Problems with a build's constraint_matches.rpt against the XDC it
    compiled (empty = every constraint bound exactly its objects)."""
    try:
        want = {(n, kind, query_id(rx)): (exp, ar, rx)
                for n, kind, rx, exp, ar in object_queries(xdc_text)}
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
    for key, (exp, async_reg, rx) in want.items():
        if key not in got:
            problems.append(f"XDC line {key[0]}: {key[1]} {{{rx}}} not in {REPORT}")
            continue
        n, exp_rec, ar = got[key]
        if exp_rec != exp:
            problems.append(f"XDC line {key[0]}: the report required {exp_rec}, the XDC "
                            f"requires {exp}")
        if n != exp:
            problems.append(f"XDC line {key[0]}: {key[1]} {{{rx}}} matched {n} "
                            f"object(s), required {exp}")
        if async_reg and ar != str(exp):
            problems.append(f"XDC line {key[0]}: ASYNC_REG set on {ar} of {exp} cells")
    for key in set(got) - set(want):
        problems.append(f"{REPORT} names a query the XDC does not hold: line {key[0]}")
    return problems


# ---- after route: the constraints do what they are for (plan099) -----------
# A count of 2 cells and 1 pin is necessary, not sufficient. On the ROUTED
# design the build must also show:
#   uart_stage1   the pin query's cell is THE flop uart_rxd drives (its only
#                 endpoint), and the async-cell query is exactly that flop and
#                 the one its Q drives (stage 2)
#   uart_false    every timing path from uart_rxd ends at stage 1's D and is a
#                 False Path
#   uart_s1s2     the stage1 -> stage2 path is analysed: one path, no
#                 exception, slack >= 0
#   uart_down     the same-clock paths out of stage 2 are analysed: at least
#                 one, none excepted, slack >= 0
# plus exceptions.rpt (report_exceptions), which the publisher reconciles
# with the XDC: exactly its set_false_path lines, nothing else.
ROUTE_CHECKS = ("uart_stage1", "uart_false", "uart_s1s2", "uart_down")
EXCEPTIONS = "exceptions.rpt"
_UART_CELLS = r".*g_uart\.u_uart/rx_q_reg\[[01]\]"
_UART_PIN = r".*g_uart\.u_uart/rx_q_reg\[0\]/D"


def tcl_route_checks(report_path: str, exceptions_path: str) -> str:
    """Tcl run after route_design: appends CHECK lines to the constraint
    report and exits 3 if any fails."""
    return "\n".join([
        f"set rc_fh [open {{{report_path}}} a]", "set rc_bad 0",
        "proc rc_put {fh name ok detail} { upvar rc_bad bad; "
        'puts $fh "CHECK\\t$name\\t$ok\\t$detail"; if {!$ok} { incr bad; '
        'puts "CONSTRAINT_EFFECT_REFUSED $name: $detail" } }',
        f"set rc_cells [get_cells -quiet -hier -regexp {{{_UART_CELLS}}}]",
        f"set rc_pin [get_pins -quiet -hier -regexp {{{_UART_PIN}}}]",
        "set rc_s1 [get_cells -quiet -of_objects $rc_pin]",
        "set rc_fan [all_fanout -from [get_ports uart_rxd] -flat -endpoints_only -only_cells]",
        "set rc_s2 {}; if {[llength $rc_s1] == 1} { set rc_s2 [all_fanout -from "
        "[get_pins -of_objects $rc_s1 -filter {REF_PIN_NAME == Q}] -flat -endpoints_only "
        "-only_cells] }",
        "set rc_ok [expr {[llength $rc_s1] == 1 && [llength $rc_fan] == 1 && "
        "[string equal $rc_fan $rc_s1] && [llength $rc_s2] == 1 && "
        "[lsort [concat $rc_s1 $rc_s2]] eq [lsort $rc_cells]}]",
        'rc_put $rc_fh uart_stage1 $rc_ok "stage1 $rc_s1; uart_rxd drives $rc_fan; '
        'stage2 $rc_s2; async cells $rc_cells"',
        "set rc_p [get_timing_paths -from [get_ports uart_rxd] -max_paths 10 -nworst 10]",
        "set rc_ok [expr {[llength $rc_p] >= 1}]; set rc_d {}",
        "foreach x $rc_p { set e [get_property EXCEPTION $x]; set t [get_property ENDPOINT_PIN $x]; "
        "lappend rc_d \"$t:$e\"; if {$e ne {False Path} || $t ne $rc_pin} { set rc_ok 0 } }",
        'rc_put $rc_fh uart_false $rc_ok "[llength $rc_p] path(s): $rc_d"',
        "set rc_p [get_timing_paths -from $rc_s1 -to $rc_s2 -max_paths 1]",
        "set rc_ok [expr {[llength $rc_p] == 1}]; set rc_d {}",
        "foreach x $rc_p { set e [get_property EXCEPTION $x]; set sl [get_property SLACK $x]; "
        "set rc_d \"slack $sl exception {$e}\"; if {$e ne {} || $sl eq {} || $sl < 0} { set rc_ok 0 } }",
        'rc_put $rc_fh uart_s1s2 $rc_ok "[llength $rc_p] path: $rc_d"',
        "set rc_p [get_timing_paths -from $rc_s2 -max_paths 20 -nworst 1]",
        "set rc_ok [expr {[llength $rc_p] >= 1}]; set rc_w {}",
        "foreach x $rc_p { set e [get_property EXCEPTION $x]; set sl [get_property SLACK $x]; "
        "if {$rc_w eq {} || ($sl ne {} && $sl < $rc_w)} { set rc_w $sl }; "
        "if {$e ne {} || $sl eq {} || $sl < 0} { set rc_ok 0 } }",
        'rc_put $rc_fh uart_down $rc_ok "[llength $rc_p] path(s), worst slack $rc_w, none excepted"',
        'puts $rc_fh "ROUTE_END\\t$rc_bad"', "close $rc_fh",
        f"report_exceptions -file {{{exceptions_path}}}",
        'if {$rc_bad} { puts "CONSTRAINT_EFFECT_REFUSED: $rc_bad check(s) failed"; exit 3 }',
    ])


_FALSE = re.compile(r"^\s*set_false_path\s+(.*?)\s*$")


def expected_exceptions(xdc_text: str) -> list:
    """[(from, to)] of the XDC's set_false_path lines, as report_exceptions
    prints them ('*' where an end is not given)."""
    out = []
    for line in xdc_text.splitlines():
        m = _FALSE.match(line.split("#", 1)[0])
        if not m:
            continue
        args = m.group(1)
        frm = re.search(r"-from\s+(\[[^\]]*\])", args)
        to = re.search(r"-to\s+(\[get_\w+\s+(?:-\w+\s+)*\{[^}]*\}\]|\[[^\]]*\])", args)
        out.append((frm.group(1) if frm else "*", to.group(1) if to else "*"))
    if re.search(r"set_(multicycle_path|max_delay|min_delay|clock_groups|disable_timing)",
                 xdc_text):
        raise Refused("the XDC holds an exception kind expected_exceptions() does not model")
    return out


def reported_exceptions(report_text: str) -> list:
    rows, on = [], False
    for line in report_text.splitlines():
        if line.startswith("--------"):
            on = True
            continue
        if not on or not line.strip():
            continue
        f = re.split(r"\s{2,}", line.strip())
        if len(f) >= 6 and f[0].isdigit():
            rows.append((f[1], f[3], f[4], f[5]))
    return rows


def check_route(report_text: str, exceptions_text: str, xdc_text: str) -> list:
    """The publisher's check of the after-route section and the exception list."""
    probs = []
    checks = {}
    end = None
    for line in report_text.splitlines():
        f = line.split("\t")
        if f[0] == "CHECK" and len(f) >= 3:
            checks[f[1]] = (f[2], f[3] if len(f) > 3 else "")
        elif f[0] == "ROUTE_END" and len(f) == 2:
            end = int(f[1])
    if end is None:
        probs.append(f"{REPORT} has no after-route section (ROUTE_END)")
    for name in ROUTE_CHECKS:
        if name not in checks:
            probs.append(f"after-route check {name} is missing")
        elif checks[name][0] != "1":
            probs.append(f"after-route check {name} failed: {checks[name][1]}")
    try:
        want = sorted(expected_exceptions(xdc_text))
    except Refused as exc:
        return probs + [str(exc)]
    got = reported_exceptions(exceptions_text)
    got_ft = sorted((f, t) for f, t, _s, _h in got)
    if got_ft != want:
        probs.append(f"{EXCEPTIONS}: the design's exceptions {got_ft} are not exactly the "
                     f"XDC's set_false_path lines {want}")
    for f, t, setup, hold in got:
        if (setup, hold) != ("false", "false"):
            probs.append(f"{EXCEPTIONS}: {f} -> {t} is {setup}/{hold}, not a false path")
    return probs


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
