#!/usr/bin/env python3
"""Distil rtl-sketch/build/area/ms_* into fpga/reports/mode_sweep.txt.
Run by fpga/scripts/mode_sweep.sh; separate so the table can be rebuilt
without re-running 27 syntheses."""
import json, glob, subprocess, os, sys
ROOT=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
B=os.path.join(ROOT,'rtl-sketch','build','area')
sys.path.insert(0,os.path.join(ROOT,'tools'))
import refusal
class Refused(refusal.Refused):
    """A synthesis result that parses but cannot be read."""
def dff(t):
    return subprocess.run(['grep','-oE','gf180mcu_fd_sc_mcu7t5v0__dff[a-z]*_[0-9]+',
        os.path.join(B,t,'netlist.v')],capture_output=True,text=True).stdout.count('\n')
def row(t):
    import manifest as mf
    path=os.path.join(B,t,'result.json')
    d=json.load(open(path))
    mf.require_keys(d,('total_cells','total_area_um2'),f"the synthesis result at {path}",refused=Refused)
    return d['total_cells'], d['total_area_um2'], dff(t)
def table():
    out=[]
    out.append("gf180mcu_fd_sc_mcu7t5v0, tt_025C_5v00, CELL area (yosys synth + abc -liberty,")
    out.append("klt's recipe, no dont_use). NOT placed, NOT routed. Regenerate with")
    out.append("GF180_PDK_REF=<pdk>/gf180mcuD/libs.ref fpga/scripts/mode_sweep.sh")
    out.append("")
    out.append("modal_dp -- the resonator bank alone (HR=0, OW=19)")
    out.append(f"  {'MODES':>5s} {'NUMS':>4s} {'MW':>3s} {'cells':>7s} {'um2':>10s} {'flops':>6s}")
    for t,m,n,mw in [('ms_modal_m4',4,4,4),('ms_modal_m8',8,6,4),('ms_modal_m9',9,6,4),
                     ('ms_modal_m11',11,6,4),('ms_modal_m12',12,6,4),('ms_modal_m14',14,6,4),
                     ('ms_modal_m16',16,6,4),('ms_modal_m16n11',16,11,4),('ms_modal_m16n16',16,16,4),
                     ('ms_modal_m17',17,11,5),('ms_modal_m18',18,11,5)]:
        try: c,a,f=row(t); out.append(f"  {m:5d} {n:4d} {mw:3d} {c:7d} {a:10.1f} {f:6d}")
        except FileNotFoundError: pass
    out.append("")
    out.append("  MODES 9,12,14,16 all have 1656 flops: the state arrays (y1,y2,exc,h1,h2)")
    out.append("  are mapped to a memory padded to a POWER OF TWO words. 9-16 cost the same;")
    out.append("  17 needs MW=5 and doubles the state. NUMS is a slope, not a staircase:")
    out.append("  42 flops (h1+h2) and ~4190 um2 per numerator slot.")
    out.append("")
    out.append("drum_kit -- the whole drum section (drum_dp + modal_dp)")
    out.append(f"  {'MODES':>5s} {'NUMS':>4s} {'cells':>7s} {'um2':>10s} {'flops':>6s}")
    for t,m,n in [('ms_kit_m8',8,6),('ms_kit_m11',11,6),('ms_kit_m12',12,6),('ms_kit_m14',14,6),
                  ('ms_kit_m16',16,6),('ms_kit_m16n11',16,11),('ms_kit_m18',18,11)]:
        try: c,a,f=row(t); out.append(f"  {m:5d} {n:4d} {c:7d} {a:10.1f} {f:6d}")
        except FileNotFoundError: pass
    out.append("")
    out.append("  SHIPPED kit is MODES=12 NUMS=6 (11 modes active: 6 filters, 5 bodies).")
    out.append("  drum_dp alone: " + f"{row('ms_drumdp')[0]} cells {row('ms_drumdp')[1]:.1f} um2 {row('ms_drumdp')[2]} flops")
    out.append("")
    out.append("mode_cfg_regs -- STRAWMAN for the mode configuration storage, which does")
    out.append("not exist in RTL (drum_kit takes a1/a2/amp/num as INPUT PORTS).")
    out.append(f"  {'MODES':>5s} {'cells':>7s} {'um2':>10s} {'flops':>6s}")
    for t,m in [('ms_cfg_m4',4),('ms_cfg_m8',8),('ms_cfg_m11',11),('ms_cfg_m12',12),
                ('ms_cfg_m14',14),('ms_cfg_m16',16),('ms_cfg_m18',18)]:
        try: c,a,f=row(t); out.append(f"  {m:5d} {c:7d} {a:10.1f} {f:6d}")
        except FileNotFoundError: pass
    out.append("")
    out.append("  Linear: 66 flops and ~6400 um2 per mode. Inside the 9-16 bracket this is")
    out.append("  the ENTIRE marginal cost of a mode -- the resonator itself is already paid for.")
    out.append("")
    out.append("AREA IS NOT CORRECTNESS. docs/verification-rules.md rule 3.")
    return "\n".join(out)
def main() -> int:
    try:
        text=table()
    except Refused as exc:
        print(f"mode_report: REFUSED -- {exc}")
        return 2
    open(os.path.join(ROOT,'fpga','reports','mode_sweep.txt'),'w').write(text+"\n")
    print(text)
    return 0
if __name__=='__main__':
    sys.exit(main())
