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

import argparse
import pathlib
import re
import subprocess
import sys

BOX = "repo-remote-wt-arty-bringup"
DCP = "/home/ubuntu/parasynth-arty-attempt02/build/arty/routed.dcp"
DCP_SHA256 = "fdb3b45d7d7b108bf3246a5013f7ac6171f126956a4d0fcc2af1a20e499945fe"
DRC_RPT = "/home/ubuntu/parasynth-arty-attempt02/build/arty/drc.rpt"
REMOTE_DIR = "/home/ubuntu/dsp-review"
EVIDENCE = pathlib.Path(__file__).resolve().parents[1] / (
    "fpga/reports/arty/vivado-2025.1/dsp-dpreg-evidence")

# The DPREG-4 instances exactly as printed by the bound drc.rpt are the
# required targets; since the 96aa987 hardening the list is DERIVED from
# that report at run time (see cell_targets) -- a hardcoded list could dump
# the wrong build's cells. The historical 5533d2b-era list is kept here as
# the shape reference the derivation must reproduce:
#   u_synth/u_voice/osc2_path/p{0,1,2}/pair/dec/prod0__0   (flagged)
#   u_synth/u_voice/osc2_path/p{0,1,2}/pair/dec/prod0      (sibling)
#   u_synth/u_voice/osc2_path/p{0,1,2}/pair/dec/acc0       (sibling)
#   u_synth/u_voice/voice_rate_converter/p_1_out__{0..8}   (flagged)

# The un-flagged siblings of the same decimator product stage (prod0,
# acc0) are dumped as context: acc0's P is the C-port feedback of every
# prod0__0, so acc0's PREG is load-bearing for the no-combinational-loop
# argument. CELLS is derived at run time; this constant stays empty.

# Historical 5533d2b-era target list, kept for the derivation test:
HISTORICAL_CELLS = [
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

TCL_TEMPLATE = r"""
# dsp_dpreg_extract.tcl -- READ-ONLY interrogation of the published routed
# checkpoint. No write_checkpoint, no generated reports, nothing written
# outside the session's remote directory.

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
"""


def make_tcl(dcp, remote_dir, cells):
    return TCL_TEMPLATE.replace("%DCP%", dcp).replace(
        "%REMOTE_DIR%", remote_dir).replace(
        "%CELLS%", "\n".join("    " + c for c in cells))


def flagged_from_drc(drc_text):
    """DPREG-4 instance names from the DRC body, with the summary-table
    cross-check the analyser applies (same regex, same refusals)."""
    table = re.search(
        r"^\|\s*DPREG-4\s*\|\s*Warning\s*\|.*?\|\s*(\d+)\s*\|",
        drc_text, re.M)
    blocks = re.findall(
        r"^DPREG-4#(\d+) Warning\n.*?^The DSP48E1 cell (\S+) "
        r"with the given dynamic OPMODE",
        drc_text, re.M | re.S)
    if not table or not blocks:
        raise SystemExit("REFUSED: drc.rpt has no parsable DPREG-4 findings")
    ids = [int(i) for i, _ in blocks]
    names = [n for _, n in blocks]
    if ids != list(range(1, len(ids) + 1)):
        raise SystemExit(f"REFUSED: DPREG-4 ids not contiguous: {ids}")
    if int(table.group(1)) != len(names):
        raise SystemExit(
            f"REFUSED: summary says {table.group(1)} DPREG-4 violations, "
            f"body lists {len(names)}")
    return names


def cell_targets(flagged):
    """Flagged instances plus the dumped siblings, refusing unknown shapes.

    A drc.rpt that flags instances outside the two known families means
    the netlist mapping changed in a way this tool cannot assume: STOP,
    never guess."""
    cells = list(flagged)
    for name in flagged:
        if name.endswith("/prod0__0"):
            base = name[:-len("prod0__0")]
            cells += [base + "prod0", base + "acc0"]
        elif re.search(r"/p_1_out(__\d+)?$", name):
            pass
        else:
            raise SystemExit(
                "REFUSED: drc.rpt flags an instance shape this derivation "
                "does not know: " + name)
    return cells


def sh(cmd, **kw):
    print("+", " ".join(str(c) for c in cmd), flush=True)
    return subprocess.run(cmd, text=True, **kw)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--box", default=BOX,
                    help="remote Vivado host (default: the historical one)")
    ap.add_argument("--dcp", default=DCP, help="routed checkpoint on the box")
    ap.add_argument("--dcp-sha256", default=DCP_SHA256,
                    help="expected SHA-256 of the DCP, asserted on the box")
    ap.add_argument("--drc-rpt", default=DRC_RPT, help="drc.rpt on the box")
    ap.add_argument("--remote-dir", default=REMOTE_DIR,
                    help="session directory on the box")
    ap.add_argument("--evidence", type=pathlib.Path, default=EVIDENCE,
                    help="local evidence output directory")
    a = ap.parse_args(argv)
    evidence = a.evidence
    evidence.mkdir(parents=True, exist_ok=True)

    # ---- phase 1: assert preconditions on the box ----
    stage = (
        "set -euo pipefail\n"
        f"actual=$(sha256sum {a.dcp} | awk '{{print $1}}')\n"
        f'if [ "$actual" != "{a.dcp_sha256}" ]; then '
        'echo "REFUSED: DCP hash mismatch: $actual"; exit 42; fi\n'
        f'mkdir -p {a.remote_dir}\n'
        f"sha256sum {a.drc_rpt} | tee {a.remote_dir}/drc_rpt.sha256\n"
        f"grep -n 'DPREG-4' {a.drc_rpt} > {a.remote_dir}/drc_dpreg_names.txt || true\n"
        "echo PRECONDITIONS_OK\n"
    )
    r = sh(["ssh", a.box, "bash -s"], input=stage)
    if r.returncode != 0:
        print("REFUSED: box preconditions failed")
        return 1

    # ---- phase 1.5: bind the targets to THIS build's drc.rpt ----
    # the DRC body is fetched and kept as evidence: the analyser re-parses
    # it and hash-binds it through the box manifest's drc_rpt.sha256 plus
    # the grep-identity check (report_drc embeds a timestamp, so the bytes
    # are bound box-side, the DPREG body both sides)
    r = sh(["ssh", a.box, f"cat {a.drc_rpt}"],
           capture_output=True)
    if r.returncode != 0:
        print("REFUSED: could not fetch drc.rpt")
        return 1
    drc_local = evidence.parent / "drc.rpt"
    drc_local.write_text(r.stdout)
    # the bytes just fetched must be the bytes the box hashed in phase 1
    # (re-checked against drc_rpt.sha256 after the pull in phase 4)
    import hashlib
    fetched = hashlib.sha256(r.stdout.encode()).hexdigest()
    # derive the target cells from THIS report, refusing unknown shapes
    flagged = flagged_from_drc(r.stdout)
    cells = cell_targets(flagged)
    print(f"DPREG-4 flagged  : {len(flagged)}")
    print(f"cells to dump    : {len(cells)} (flagged + siblings)")

    tcl_local = evidence / "dsp_dpreg_extract.tcl"
    tcl_local.write_text(make_tcl(a.dcp, a.remote_dir, cells))
    r = sh(["scp", str(tcl_local), f"{a.box}:{a.remote_dir}/dsp_dpreg_extract.tcl"])
    if r.returncode != 0:
        print("REFUSED: could not stage Tcl")
        return 1

    # ---- phase 2: run under the shared vivado lock ----
    run = (
        "set -euo pipefail\n"
        "source /tools/Xilinx/2025.1/Vivado/settings64.sh >/dev/null 2>&1\n"
        f"cd {a.remote_dir}\n"
        "rm -f dsp_cells_dump.txt vivado_extract.log vivado_extract.jou\n"
        "flock -w 7200 /home/ubuntu/vivado.lock "
        "vivado -mode batch -source dsp_dpreg_extract.tcl "
        "-log vivado_extract.log -journal vivado_extract.jou\n"
    )
    r = sh(["ssh", a.box, "bash -s"], input=run)
    if r.returncode != 0:
        print(f"REFUSED: vivado batch failed rc={r.returncode}")
        sh(["ssh", a.box, f"tail -40 {a.remote_dir}/vivado_extract.log || true"])
        return 1

    # ---- phase 3: manifest + pull back ----
    r = sh(["ssh", a.box,
            f"cd {a.remote_dir} && sha256sum dsp_cells_dump.txt vivado_extract.log "
            "drc_dpreg_names.txt drc_rpt.sha256 > MANIFEST.sha256 && cat MANIFEST.sha256 && "
            "grep -c '^ERROR' vivado_extract.log || true"])
    if r.returncode != 0:
        print("REFUSED: manifest failed")
        return 1

    for f in ("dsp_cells_dump.txt", "vivado_extract.log", "drc_dpreg_names.txt",
              "MANIFEST.sha256", "drc_rpt.sha256"):
        r = sh(["scp", f"{a.box}:{a.remote_dir}/{f}", str(evidence / f)])
        if r.returncode != 0:
            print(f"REFUSED: could not pull {f}")
            return 1

    # ---- phase 4: verify locally against the box manifest ----
    bad = []
    for line in (evidence / "MANIFEST.sha256").read_text().splitlines():
        h, name = line.split(None, 1)
        name = name.strip().lstrip("*")
        local = evidence / name
        got = hashlib.sha256(local.read_bytes()).hexdigest()
        status = "OK" if got == h else "MISMATCH"
        print(f"{status} {name} {got}")
        if got != h:
            bad.append(name)
    if bad:
        print(f"REFUSED: local copies differ from box manifest: {bad}")
        return 1
    # and the drc.rpt the targets were derived from is the box's bytes
    box_hash = next((l.split()[0]
                     for l in (evidence / "drc_rpt.sha256").read_text().splitlines()
                     if l.endswith("drc.rpt")), None)
    if box_hash != fetched:
        print(f"REFUSED: fetched drc.rpt {fetched} != box drc.rpt {box_hash}")
        return 1

    dump = (evidence / "dsp_cells_dump.txt").read_text()
    print(f"cells dumped      : {dump.count('==== CELL ')}")
    print(f"missing instances : {dump.count('MISSING ')}")
    print(f"extraction OK, evidence in {evidence}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
