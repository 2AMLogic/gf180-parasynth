#!/usr/bin/env python3
"""Extract per-instance DSP48E1 evidence for the 13 DPREG-4 cells.

Runs on the remote Vivado box (repo-remote-wt-arty-bringup) against the
PUBLISHED routed checkpoint, strictly READ-ONLY: open_checkpoint is used to
query the netlist; no write_checkpoint, no report writes outside this
session's own directory (/home/ubuntu/dsp-review).

REFUSED is a first-class outcome:
  * the DCP SHA-256 is asserted ON THE BOX before Vivado opens it;
  * the Vivado version is asserted inside the Tcl;
  * every one of the 13 instance names must be found exactly once;
  * the pulled artifacts are re-hashed locally against the box manifest.

Anything else reports REFUSED with the exact evidence and exits non-zero.

Output: fpga/reports/arty/vivado-2025.1/dsp-dpreg-evidence/
  dsp_cells_dump.txt   per-cell properties, pins, drivers, OPMODE control cone
  vivado_extract.log   full Vivado batch log
  drc_dpreg_names.txt  DPREG-4 section of the BOX's drc.rpt (name cross-check)
  MANIFEST.sha256      sha256 of the three artifacts, computed on the box
"""

import pathlib
import subprocess
import sys

BOX = "repo-remote-wt-arty-bringup"
DCP = "/home/ubuntu/parasynth-arty-attempt02/build/arty/routed.dcp"
DCP_SHA256 = "fdb3b45d7d7b108bf3246a5013f7ac6171f126956a4d0fcc2af1a20e499945fe"
DRC_RPT = "/home/ubuntu/parasynth-arty-attempt02/build/arty/drc.rpt"
REMOTE_DIR = "/home/ubuntu/dsp-review"
EVIDENCE = pathlib.Path(__file__).resolve().parents[1] / (
    "fpga/reports/arty/vivado-2025.1/dsp-dpreg-evidence")

# The 13 DPREG-4 instances exactly as printed by the published drc.rpt
# (DPREG-4#1 .. #13), plus the un-flagged siblings of the same decimator
# product stage (prod0, acc0) whose mapping carries the rest of the
# accumulate: acc0's P is the C-port feedback of every prod0__0, so acc0's
# PREG is load-bearing for the no-combinational-loop argument.
CELLS = [
    "u_synth/u_voice/osc2_path/p0/pair/dec/prod0__0",
    "u_synth/u_voice/osc2_path/p1/pair/dec/prod0__0",
    "u_synth/u_voice/osc2_path/p2/pair/dec/prod0__0",
    "u_synth/u_voice/osc2_path/p0/pair/dec/prod0",
    "u_synth/u_voice/osc2_path/p1/pair/dec/prod0",
    "u_synth/u_voice/osc2_path/p2/pair/dec/prod0",
    "u_synth/u_voice/osc2_path/p0/pair/dec/acc0",
    "u_synth/u_voice/osc2_path/p1/pair/dec/acc0",
    "u_synth/u_voice/osc2_path/p2/pair/dec/acc0",
    "u_synth/u_voice/voice_rate_converter/p_1_out",
    "u_synth/u_voice/voice_rate_converter/p_1_out__0",
    "u_synth/u_voice/voice_rate_converter/p_1_out__1",
    "u_synth/u_voice/voice_rate_converter/p_1_out__2",
    "u_synth/u_voice/voice_rate_converter/p_1_out__3",
    "u_synth/u_voice/voice_rate_converter/p_1_out__4",
    "u_synth/u_voice/voice_rate_converter/p_1_out__5",
    "u_synth/u_voice/voice_rate_converter/p_1_out__6",
    "u_synth/u_voice/voice_rate_converter/p_1_out__7",
    "u_synth/u_voice/voice_rate_converter/p_1_out__8",
]

TCL = r"""
# dsp_dpreg_extract.tcl -- READ-ONLY interrogation of the published routed
# checkpoint. No write_checkpoint, no generated reports, nothing written
# outside /home/ubuntu/dsp-review.

if {![string match "2025.1*" [version -short]]} {
    puts "REFUSED: expected Vivado 2025.1, got [version -short]"
    exit 42
}

set dcp "%DCP%"
set cells {
%CELLS%
}

open_checkpoint $dcp

set out [open "%REMOTE_DIR%/dsp_cells_dump.txt" w]
proc emit {s} { global out; puts $out $s }

emit "VIVADO [version -short]"
emit "DCP $dcp"

set all_dsp [get_cells -hier -quiet -filter {REF_NAME == DSP48E1}]
emit "TOTAL_DSP48E1 [llength $all_dsp]"
emit "==== DSP SIBLINGS (osc2_path / voice_rate_converter) ===="
foreach d $all_dsp {
    set dn [get_property NAME $d]
    if {[string match "*osc2_path*" $dn] || [string match "*voice_rate_converter*" $dn]} {
        emit "DSP $dn"
    }
}

array unset ::seen
proc trace_cone {pins label} {
    array unset ::seen
    set work {}
    foreach p $pins {
        foreach n [get_nets -quiet -of_objects $p] { lappend work $n }
    }
    set count 0
    while {[llength $work] && $count < 3000} {
        set n [lindex $work 0]
        set work [lrange $work 1 end]
        incr count
        set nn [get_property NAME $n]
        if {[info exists ::seen($nn)]} { continue }
        set ::seen($nn) 1
        emit "CONE $label NET $nn type=[get_property TYPE $n]"
        set drvs [get_pins -quiet -leaf -of_objects $n -filter {DIRECTION == OUT}]
        if {![llength $drvs]} { emit "  driver NONE (constant or tied net)" }
        foreach d $drvs {
            set dc [get_cells -quiet -of_objects $d]
            if {![llength $dc]} { emit "  driver [get_property NAME $d] (no cell)"; continue }
            set cell [lindex $dc 0]
            set cn [get_property NAME $cell]
            set rt [get_property REF_NAME $cell]
            if {[info exists ::seen(cell:$cn)]} {
                emit "  driver $cn REF=$rt (seen)"
                continue
            }
            set ::seen(cell:$cn) 1
            emit "  driver $cn REF=$rt"
            if {[string match "LUT*" $rt]} {
                if {[catch {set init [get_property INIT $cell]}] == 0} { emit "    INIT=$init" }
                if {[catch {set eqn [get_property EQN $cell]}] == 0} { emit "    EQN=$eqn" }
                foreach ip [get_pins -quiet -of_objects $cell -filter {DIRECTION == IN}] {
                    set ipn [get_property REF_PIN_NAME $ip]
                    foreach in [get_nets -quiet -of_objects $ip] {
                        emit "    in $ipn net [get_property NAME $in]"
                        lappend work $in
                    }
                }
            } elseif {[string match "FD*" $rt] || [string match "LD*" $rt]} {
                if {[catch {set init [get_property INIT $cell]}] == 0} { emit "    FF_INIT=$init" }
                foreach ip [get_pins -quiet -of_objects $cell -filter {REF_PIN_NAME == D}] {
                    foreach in [get_nets -quiet -of_objects $ip] {
                        emit "    FF $cn D net [get_property NAME $in]"
                    }
                }
            } elseif {[string match "BUFG*" $rt] || [string match "IBUF*" $rt] \
                      || [string match "PLLE2*" $rt] || [string match "MMCME2*" $rt] \
                      || [string match "BUFH*" $rt] || [string match "BUFR*" $rt]} {
                emit "    (clock tree source, stop)"
            } else {
                foreach ip [get_pins -quiet -of_objects $cell -filter {DIRECTION == IN}] {
                    set ipn [get_property REF_PIN_NAME $ip]
                    foreach in [get_nets -quiet -of_objects $ip] {
                        emit "    in $ipn net [get_property NAME $in]"
                        lappend work $in
                    }
                }
            }
        }
    }
    if {[llength $work]} { emit "CONE $label TRUNCATED at 3000 nets" }
    emit "CONE $label END nets=$count"
}

set missing 0
foreach c $cells {
    set objs [get_cells -quiet $c]
    if {[llength $objs] != 1} {
        emit "MISSING $c count=[llength $objs]"
        incr missing
        continue
    }
    set cell [lindex $objs 0]
    emit "=================================================="
    emit "==== CELL $c ===="
    emit "---- properties ----"
    foreach p [lsort [list_property $cell]] {
        if {[catch {set v [get_property $p $cell]} err]} { set v "ERR:$err" }
        emit "$p = $v"
    }
    emit "---- pins ----"
    foreach pin [get_pins -quiet -of_objects $cell] {
        set pn [get_property REF_PIN_NAME $pin]
        set dir [get_property DIRECTION $pin]
        set netname "-"
        set drv "-"
        set nets [get_nets -quiet -of_objects $pin]
        if {[llength $nets]} {
            set n [lindex $nets 0]
            set netname [get_property NAME $n]
            set drvs [get_pins -quiet -leaf -of_objects $n -filter {DIRECTION == OUT}]
            if {[llength $drvs]} {
                set d [lindex $drvs 0]
                set dc [get_cells -quiet -of_objects $d]
                set dref "-"
                if {[llength $dc]} { set dref [get_property REF_NAME [lindex $dc 0]] }
                set drv "[get_property NAME $d] ($dref)"
            } else {
                set drv "NO-DRIVER (nettype=[get_property TYPE $n])"
            }
        }
        emit "PIN $pn dir=$dir net=$netname driver=$drv"
    }
    emit "---- P/PCOUT fanout ----"
    # Vivado -filter =~ is glob, not regex, and indexed bus pins defeat ==
    # matching; select by string match on the enumerated pin names instead.
    foreach p [get_pins -quiet -of_objects $cell] {
        set pn [get_property REF_PIN_NAME $p]
        if {![string match "P\\\[*" $pn] && ![string match "PCOUT\\\[*" $pn]} { continue }
        foreach n [get_nets -quiet -of_objects $p] {
            set loads [get_pins -quiet -leaf -of_objects $n -filter {DIRECTION == IN}]
            emit "FANOUT $pn net [get_property NAME $n] loads=[llength $loads]"
            foreach l [lrange $loads 0 7] {
                set lc [get_cells -quiet -of_objects $l]
                set lref "-"
                if {[llength $lc]} { set lref [get_property REF_NAME [lindex $lc 0]] }
                emit "  load [get_property NAME $l] ($lref)"
            }
        }
    }
    emit "---- control cones (OPMODE/ALUMODE/CARRYINSEL/CARRYIN/CE/RST) ----"
    set cone_pins {}
    foreach p [get_pins -quiet -of_objects $cell] {
        set pn [get_property REF_PIN_NAME $p]
        if {[string match "OPMODE\\\[*" $pn] || [string match "ALUMODE\\\[*" $pn] \
             || [string match "CARRYINSEL\\\[*" $pn] \
             || [lsearch -exact {CARRYIN CEA1 CEA2 CEB1 CEB2 CEC CED CEM CEP CECARRYIN CECTRL CEOPMODE RSTA RSTB RSTC RSTD RSTM RSTP RSTCARRYIN RSTCTRL RSTOPMODE} $pn] >= 0} {
            lappend cone_pins $p
        }
    }
    trace_cone $cone_pins $c
    emit "==== END CELL $c ===="
}
emit "MISSING_COUNT $missing"
close $out
if {$missing > 0} { puts "REFUSED: $missing of [llength $cells] cells not found"; exit 43 }
puts "EXTRACT_OK"
""".replace("%DCP%", DCP).replace("%REMOTE_DIR%", REMOTE_DIR).replace(
    "%CELLS%", "\n".join("    " + c for c in CELLS))


def sh(cmd, **kw):
    print("+", " ".join(str(c) for c in cmd), flush=True)
    return subprocess.run(cmd, text=True, **kw)


def main():
    EVIDENCE.mkdir(parents=True, exist_ok=True)

    # ---- phase 1: assert preconditions on the box, stage the Tcl ----
    stage = (
        "set -euo pipefail\n"
        f"actual=$(sha256sum {DCP} | awk '{{print $1}}')\n"
        f'if [ "$actual" != "{DCP_SHA256}" ]; then '
        'echo "REFUSED: DCP hash mismatch: $actual"; exit 42; fi\n'
        f'mkdir -p {REMOTE_DIR}\n'
        f"sha256sum {DRC_RPT} | tee {REMOTE_DIR}/drc_rpt.sha256\n"
        f"grep -n 'DPREG-4' {DRC_RPT} > {REMOTE_DIR}/drc_dpreg_names.txt || true\n"
        "echo PRECONDITIONS_OK\n"
    )
    r = sh(["ssh", BOX, "bash -s"], input=stage)
    if r.returncode != 0:
        print("REFUSED: box preconditions failed")
        return 1

    tcl_local = EVIDENCE / "dsp_dpreg_extract.tcl"
    tcl_local.write_text(TCL)
    r = sh(["scp", str(tcl_local), f"{BOX}:{REMOTE_DIR}/dsp_dpreg_extract.tcl"])
    if r.returncode != 0:
        print("REFUSED: could not stage Tcl")
        return 1

    # ---- phase 2: run under the shared vivado lock ----
    run = (
        "set -euo pipefail\n"
        "source /tools/Xilinx/2025.1/Vivado/settings64.sh >/dev/null 2>&1\n"
        f"cd {REMOTE_DIR}\n"
        "rm -f dsp_cells_dump.txt vivado_extract.log vivado_extract.jou\n"
        "flock -w 7200 /home/ubuntu/vivado.lock "
        "vivado -mode batch -source dsp_dpreg_extract.tcl "
        "-log vivado_extract.log -journal vivado_extract.jou\n"
    )
    r = sh(["ssh", BOX, "bash -s"], input=run)
    if r.returncode != 0:
        print(f"REFUSED: vivado batch failed rc={r.returncode}")
        sh(["ssh", BOX, f"tail -40 {REMOTE_DIR}/vivado_extract.log || true"])
        return 1

    # ---- phase 3: manifest + pull back ----
    r = sh(["ssh", BOX,
            f"cd {REMOTE_DIR} && sha256sum dsp_cells_dump.txt vivado_extract.log "
            "drc_dpreg_names.txt drc_rpt.sha256 > MANIFEST.sha256 && cat MANIFEST.sha256 && "
            "grep -c '^ERROR' vivado_extract.log || true"])
    if r.returncode != 0:
        print("REFUSED: manifest failed")
        return 1

    for f in ("dsp_cells_dump.txt", "vivado_extract.log", "drc_dpreg_names.txt",
              "MANIFEST.sha256", "drc_rpt.sha256"):
        r = sh(["scp", f"{BOX}:{REMOTE_DIR}/{f}", str(EVIDENCE / f)])
        if r.returncode != 0:
            print(f"REFUSED: could not pull {f}")
            return 1

    # ---- phase 4: verify locally against the box manifest ----
    bad = []
    for line in (EVIDENCE / "MANIFEST.sha256").read_text().splitlines():
        h, name = line.split(None, 1)
        name = name.strip().lstrip("*")
        local = EVIDENCE / name
        import hashlib
        got = hashlib.sha256(local.read_bytes()).hexdigest()
        status = "OK" if got == h else "MISMATCH"
        print(f"{status} {name} {got}")
        if got != h:
            bad.append(name)
    if bad:
        print(f"REFUSED: local copies differ from box manifest: {bad}")
        return 1

    dump = (EVIDENCE / "dsp_cells_dump.txt").read_text()
    print(f"cells dumped      : {dump.count('==== CELL ')}")
    print(f"missing instances : {dump.count('MISSING ')}")
    print(f"extraction OK, evidence in {EVIDENCE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
