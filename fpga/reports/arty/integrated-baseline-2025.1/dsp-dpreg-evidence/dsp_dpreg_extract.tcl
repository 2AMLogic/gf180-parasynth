
# dsp_dpreg_extract.tcl -- READ-ONLY interrogation of the published routed
# checkpoint. No write_checkpoint, no generated reports, nothing written
# outside the session's remote directory.

if {![string match "2025.1*" [version -short]]} {
    puts "REFUSED: expected Vivado 2025.1, got [version -short]"
    exit 42
}

set dcp "/home/ubuntu/integrated-baseline/build/arty/routed.dcp"
set cells {
    u_synth/u_voice/osc2_path/p0/pair/dec/prod0__0
    u_synth/u_voice/osc2_path/p1/pair/dec/prod0__0
    u_synth/u_voice/osc2_path/p2/pair/dec/prod0__0
    u_synth/u_voice/voice_rate_converter/p_1_out
    u_synth/u_voice/voice_rate_converter/p_1_out__0
    u_synth/u_voice/voice_rate_converter/p_1_out__1
    u_synth/u_voice/voice_rate_converter/p_1_out__2
    u_synth/u_voice/voice_rate_converter/p_1_out__3
    u_synth/u_voice/voice_rate_converter/p_1_out__4
    u_synth/u_voice/voice_rate_converter/p_1_out__5
    u_synth/u_voice/voice_rate_converter/p_1_out__6
    u_synth/u_voice/voice_rate_converter/p_1_out__7
    u_synth/u_voice/voice_rate_converter/p_1_out__8
    u_synth/u_voice/osc2_path/p0/pair/dec/prod0
    u_synth/u_voice/osc2_path/p0/pair/dec/acc0
    u_synth/u_voice/osc2_path/p1/pair/dec/prod0
    u_synth/u_voice/osc2_path/p1/pair/dec/acc0
    u_synth/u_voice/osc2_path/p2/pair/dec/prod0
    u_synth/u_voice/osc2_path/p2/pair/dec/acc0
}

open_checkpoint $dcp

set out [open "/home/ubuntu/dsp-review-dcpbind/dsp_cells_dump.txt" w]
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
