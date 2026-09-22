#!/usr/bin/env python3
"""Directed mapped-DSP comparison for the DPREG-4 disposition.

Simulates DSP48E1 primitives with the parameterization EXTRACTED from the
published routed checkpoint (dsp_cells_dump.txt), driven by the control
sequences the RTL can actually issue, against a Python golden implementing
the UG479 arithmetic of a 25x18 signed multiply-accumulate with the same
pipeline registers.

SIMULATOR NOTE (2026-09-22). The brief's primary path was xsim on the remote
box. Mid-session the box's root volume was replaced (cloud-init rebuild,
uptime reset, /tools/Xilinx and the routed.dcp copy vanished; df changed from
194G/143G-used to 97G/5.0G-used). This script therefore uses the brief's
authorized fallback: Icarus Verilog with the Xilinx unisim library, taken
from the VENDOR'S OWN public repository Xilinx/XilinxUnisimLibrary, commit
1c8e05fd1e9a79ceb8b996a0996674122eed086f (DSP48E1.v sha256 pinned in
unisim/DSP48E1.v.sha256). Limitation, labelled per docs/failure-modes.md:
that mirror is version 2016.1 of the model, not the copy Vivado 2025.1
ships; it is an external referent, not a self-consistent one, because the
Python golden below was written independently from UG479 and the two must
agree bit-exact on the clean run.

Two instances, wired as extracted (p0 path; p1/p2 are structurally identical):

  D2 "prod0__0"  AREG=2 BREG=0 CREG=0 MREG=0 PREG=0 OPMODEREG=0
                 A <- 16-bit signed stimulus, sign-extended to A[29:0]
                 B <- 18'd14712 (h[15], a routed constant)
                 OPMODE[5:4] <- (tap != 31)   [LUT5 prod0__0_i_1]
                 OPMODE[2:0] <- {tap[0],0,tap[0]} [LUT5 prod0__0_i_2]
                 => P = 2M on odd taps, 0 on even taps, Z mux 000/011 (both 0)
  D1 "acc0"      AREG=1 BREG=1 CREG=1 MREG=0 PREG=0 OPMODEREG=0
                 OPMODE = 7'b0001011, constant (Z=PCIN, X=A:B, Y=0)
                 A:B <- own P[47:0] (identity map, asserted below)
                 PCIN <- D2.PCOUT (the dedicated cascade)
                 => registered accumulate: acc(t+1) = acc(t) + product(t)

The idle/hold tap values follow the RTL FSM exactly: tap resets to 0 and
holds 15 between frames; 31 is unreachable and only appears as the LUTs'
out-of-range constant.

Coverage: signed positive/negative values, +/−full-scale rails, reset
asserted mid-stream (D2.RSTA and D1.RSTA/RSTB), idle holds, and every
OPMODE/RESET transition the RTL can issue.

RED-FIRST (docs/verification-rules.md rule 1): controls run BEFORE the clean
sequence and each must turn the harness red with a recorded count, or the
run REFUSES:
  mode 1 pregflip -- D2.PREG flipped 0->1: P registers one cycle late, the
                     cascade lags, the accumulate diverges. Class: WRONG VALUE.
  mode 2 opmodep  -- every tap==7 cycle forces OPMODE[6:4]=010 (Z=P), the
                     unsupported feedback DPREG-4 warns about. With PREG=0
                     this is a combinational P->Z->P loop. Class: X
                     (oscillation/unknown).
Only after both controls have fired does mode 0 (the extracted, routed
configuration) run; it must report 0 X and 0 wrong-value cycles.
"""

import hashlib
import json
import pathlib
import re
import shutil
import subprocess
import sys

EVIDENCE = pathlib.Path(__file__).resolve().parents[1] / (
    "fpga/reports/arty/vivado-2025.1/dsp-dpreg-evidence")
DSIM = EVIDENCE / "dsim"
UNISIM = EVIDENCE / "unisim"

DUMP = EVIDENCE / "dsp_cells_dump.txt"
ACC0 = "u_synth/u_voice/osc2_path/p0/pair/dec/acc0"

B_CONST = 14712          # D2 B port constant (h[15]), from the routed pins
MASK48 = (1 << 48) - 1


# ---------------------------------------------------------------- stimulus
def build_stimulus():
    """(cea, rsta2, a2 s16, c2 s48, tap, ce1, rst1) per cycle.

    c2 is the C-port stimulus of D2: the routed C net carries prod0.P (the
    paired-tap product, a feed-forward DSP -- driver confirmed in the
    extraction), so it is an input sequence here, not a feedback.
    RTL FSM shape: reset window (tap holds 0), then frames of 16 busy cycles
    tap=0..15 with the sample-accept strobe high; between frames the tap
    register HOLDS 15 (idle) -- 31 is unreachable in the RTL.
    """
    rows = []
    rows += [(0, 1, 0, 0, 0, 0, 1)] * 4         # reset window, tap holds 0
    small = [1, -1, 2, -2, 3, -3]
    rails = [32767, -32768]
    seed = 987654321
    rnd = []
    for _ in range(48):
        seed = seed * 1103515245 % 2147483647
        rnd.append(seed % 65536 - 32768)
    patterns = [
        small * 3,                                  # f0 small +
        [-v for v in small] * 3,                    # f1 small -
        rails * 8,                                  # f2 full-scale rails
        [12345, -12345, 8191, -8192, 5, -5] * 3,    # f3 mixed + mid-stream rst
        [257, -257, 6553, -6553, 4096, -4096] * 3,  # f4
        rnd,                                        # f5 pseudo-random
        rails * 8,                                  # f6 rails after reset
        [32767, 32767, -32768, -32768, 777, -777] * 3,  # f7 mixed
    ]
    c2pat = [12345678, -12345678, 2**30, -(2**30), 1, -1]  # incl. wide rails
    for f, pat in enumerate(patterns):
        rows.append((0, 0, 0, 0, 15, 0, 0))         # idle: tap holds 15
        for tap in range(16):
            mid_rst = 1 if (f == 3 and tap < 3) else 0
            rows.append((1, 0, pat[tap % len(pat)],
                         c2pat[(tap + f) % len(c2pat)], tap,
                         1 if tap < 15 else 0, mid_rst))
        rows.append((0, 0, 0, 0, 15, 0, 0))
    return rows


# ------------------------------------------------------------------ golden
def sext(v, w):
    return v - (1 << w) if v & (1 << (w - 1)) else v


def golden(rows, mode):
    """UG479 semantics of the two extracted DSP48E1 instances.

    mode 0: the routed configuration (both PREG=0)
    mode 1: control -- D2.PREG flipped to 1
    mode 2: control -- OPMODE Z=P on tap==7. The golden computes the loop-free
            arithmetic with the same Z constant; it cannot model the
            oscillation the true feedback creates, and is not expected to
            match: the recorded divergence IS the control evidence.
    """
    # vendor unisim mux tables (the executable truth; see disposition):
    #   X (OPMODE[1:0]): 00=0 01=M 10=P 11=A:B
    #   Y (OPMODE[3:2]): 00=0 01=0 10=ones 11=C   -- 01 is ZERO, not M
    #   Z (OPMODE[6:4]): 000=0 001=PCIN 010=P 011=C 100=P 1x1=P>>17
    # P is an operand only for X=10 or Z in {010,100,110,111}.
    a_r1 = a_r2 = 0            # D2 A pipeline (AREG=2, CEA1=CEA2 together)
    p2_reg = 0                 # only used by the PREG flip control
    acc1 = 0                   # D1 registered A:B feedback (AREG=BREG=1)
    out = []
    for cea, rsta2, a2, c2, tap, ce1, rst1 in rows:
        a2 &= 0xFFFF
        c2 &= MASK48
        if mode == 2 and tap == 7:
            z = 2               # forced Z=P: the unsupported feedback
        else:
            z = c2 if (tap != 31) else 0   # OPMODE[6:4]={000,011}: 0 or C
        m = sext(a_r2 & 0x1FFFFFF, 25) * B_CONST   # 25x18 signed multiply
        x = m if (tap & 1) else 0                  # OPMODE[1:0] = {0, tap[0]}
        y = 0                                      # OPMODE[3:2] = {00, 01}
        p2_comb = (z + x + y) & MASK48
        p2_feed = p2_reg if mode == 1 else p2_comb
        p1_comb = (p2_feed + acc1) & MASK48
        out.append((p2_reg if mode == 1 else p2_comb, p1_comb))
        # clock edge
        if rsta2:
            a_r1 = a_r2 = 0
        elif cea:
            a_r2 = a_r1
            a_r1 = sext(a2, 16) & 0x3FFFFFF
        if mode == 1:
            p2_reg = p2_comb
        if rst1:
            acc1 = 0
        elif ce1:
            acc1 = p1_comb
    return out


# --------------------------------------------------------------- artifacts
TB = r"""
`timescale 1ns/1ps
`default_nettype none
module dsp_dpreg_tb;
    reg clk = 1'b0;
    always #5 clk = ~clk;

    reg        ce_a2  = 1'b0;
    reg        rst_a2 = 1'b1;
    reg signed [15:0] a2s = 16'sd0;
    reg signed [47:0] c2s = 48'sd0;
    reg [4:0]  tap    = 5'd0;
    reg        ce1    = 1'b0;
    reg        rst1   = 1'b1;
    reg        xflag;
    integer f, fo, rc;
    integer cea_i, rsta_i, a2_i, tap_i, ce1_i, rst1_i;
    reg [47:0] c2_i;

    wire [6:0] prod1 = {7{(tap != 5'd31)}} & 7'b0110000;
    wire [6:0] tapq  = {7{tap[0]}} & 7'b0000101;
`ifdef MODE_OPMODEP
    wire [6:0] opmode2 = (tap == 5'd7) ? {3'b010, prod1[3:0] | tapq[3:0]}
                                       : (prod1 | tapq);
`else
    wire [6:0] opmode2 = prod1 | tapq;
`endif

    wire [47:0] p2, pcout2;
    wire [47:0] p1;
    wire [29:0] a1 = p1[47:18];
    wire [17:0] b1 = p1[17:0];

    DSP48E1 #(
        .AREG(2), .ACASCREG(2), .BREG(0), .BCASCREG(0), .CREG(0), .DREG(1),
        .MREG(0),
`ifdef MODE_PREGFLIP
        .PREG(1),
`else
        .PREG(0),
`endif
        .OPMODEREG(0), .USE_DPORT("FALSE"), .MASK(48'h3FFFFFFFFFFF),
        .AUTORESET_PATDET("NO_RESET"), .SEL_MASK("MASK")
    ) D2 (
        .CLK(clk),
        .CEA1(ce_a2), .CEA2(ce_a2), .CEAD(1'b0), .CEB1(1'b0), .CEB2(1'b0),
        .CEC(1'b0), .CED(1'b0), .CEM(1'b0), .CEP(1'b0), .CECTRL(1'b0),
        .CECARRYIN(1'b0), .CEALUMODE(1'b0), .CEINMODE(1'b0),
        .RSTA(rst_a2), .RSTB(1'b0), .RSTC(1'b0), .RSTD(1'b0), .RSTM(1'b0),
        .RSTP(1'b0), .RSTCTRL(1'b0), .RSTALLCARRYIN(1'b0), .RSTALUMODE(1'b0),
        .RSTINMODE(1'b0),
        .A({{14{a2s[15]}}, a2s}), .B(18'sd%B_CONST%), .C(c2s), .D(25'b0),
        .INMODE(5'b0), .OPMODE(opmode2), .ALUMODE(4'b0),
        .CARRYIN(1'b0), .CARRYINSEL(3'b0), .CARRYCASCIN(1'b0),
        .MULTSIGNIN(1'b0), .PCIN(48'b0),
        .P(p2), .PCOUT(pcout2), .CARRYCASCOUT(), .MULTSIGNOUT(),
        .OVERFLOW(), .PATTERNBDETECT(), .PATTERNDETECT(), .UNDERFLOW()
    );

    DSP48E1 #(
        .AREG(1), .ACASCREG(1), .BREG(1), .BCASCREG(1), .CREG(1), .DREG(1),
        .MREG(0), .PREG(0), .USE_MULT("NONE"),
        .OPMODEREG(0), .USE_DPORT("FALSE"), .MASK(48'h3FFFFFFFFFFF),
        .AUTORESET_PATDET("NO_RESET"), .SEL_MASK("MASK")
    ) D1 (
        .CLK(clk),
        .CEA1(1'b0), .CEA2(ce1), .CEAD(1'b0), .CEB1(1'b0), .CEB2(ce1),
        .CEC(1'b0), .CED(1'b0), .CEM(1'b0), .CEP(1'b0), .CECTRL(1'b0),
        .CECARRYIN(1'b0), .CEALUMODE(1'b0), .CEINMODE(1'b0),
        .RSTA(rst1), .RSTB(rst1), .RSTC(1'b0), .RSTD(1'b0), .RSTM(1'b0),
        .RSTP(1'b0), .RSTCTRL(1'b0), .RSTALLCARRYIN(1'b0), .RSTALUMODE(1'b0),
        .RSTINMODE(1'b0),
        .A(a1), .B(b1), .C(48'hFFFFFFFFFFFF), .D(25'b0),
        .INMODE(5'b0), .OPMODE(7'b0010011), .ALUMODE(4'b0),
        .CARRYIN(1'b0), .CARRYINSEL(3'b0), .CARRYCASCIN(1'b0),
        .MULTSIGNIN(1'b0), .PCIN(pcout2),
        .P(p1), .PCOUT(), .CARRYCASCOUT(), .MULTSIGNOUT(),
        .OVERFLOW(), .PATTERNBDETECT(), .PATTERNDETECT(), .UNDERFLOW()
    );

    initial begin
        f = $fopen("stim.txt", "r");
        if (!f) begin $display("STIMULUS-OPEN-FAILED"); $finish; end
        fo = $fopen("actual.txt", "w");
        while (!$feof(f)) begin
            @(negedge clk);
            rc = $fscanf(f, "%d %d %d %h %d %d %d",
                         cea_i, rsta_i, a2_i, c2_i, tap_i, ce1_i, rst1_i);
            if (rc == 7) begin
                ce_a2  <= cea_i[0];
                rst_a2 <= rsta_i[0];
                a2s    <= a2_i[15:0];
                c2s    <= c2_i;
                tap    <= tap_i[4:0];
                ce1    <= ce1_i[0];
                rst1   <= rst1_i[0];
                #1;
                xflag = ((^p2) === 1'bx) || ((^p1) === 1'bx);
                $fwrite(fo, "%h %h %0d\n", p2 & 48'hFFFFFFFFFFFF,
                        p1 & 48'hFFFFFFFFFFFF, xflag ? 1 : 0);
            end
        end
        $fclose(f);
        $fclose(fo);
        $display("TB-DONE");
        $finish;
    end
endmodule
`default_nettype wire
""".replace("%B_CONST%", str(B_CONST))

RUN = r"""
set -e
cd {dsim}
# glbl stub: GSR pulses high for 1 ns (reg init) then releases, so the
# unisim pipeline registers start at 0 exactly as the golden assumes. The
# vendor glbl.v's 100 ns ROC would override clocking mid-stimulus.
cat > glbl_stub.v <<'EOF'
`timescale 1ns/1ps
module glbl;
    reg GSR = 1'b1;
    initial begin #1; GSR = 1'b0; end
endmodule
EOF
for mode in 1 2 0; do
  case $mode in
    1) defs="-D MODE_PREGFLIP";;
    2) defs="-D MODE_OPMODEP";;
    0) defs="";;
  esac
  rm -f sim_mode$mode.vvp actual.txt actual_mode$mode.txt
  iverilog -g2005 $defs -o sim_mode$mode.vvp dsp_dpreg_tb.v \
      ../unisim/DSP48E1.v glbl_stub.v > iverilog_mode$mode.log 2>&1
  vvp -n sim_mode$mode.vvp > vvp_mode$mode.log 2>&1
  mv actual.txt actual_mode$mode.txt
done
sha256sum stim.txt dsp_dpreg_tb.v glbl_stub.v expected_mode0.txt \
    expected_mode1.txt expected_mode2.txt actual_mode0.txt actual_mode1.txt \
    actual_mode2.txt iverilog_mode0.log iverilog_mode1.log \
    iverilog_mode2.log vvp_mode0.log vvp_mode1.log vvp_mode2.log \
    > MANIFEST.sha256
cat MANIFEST.sha256
""".format(dsim=DSIM)


def run_local():
    r = subprocess.run(["bash", "-c", RUN], text=True, capture_output=True)
    print(r.stdout[-3000:], r.stderr[-1500:])
    return r.returncode


def main():
    DSIM.mkdir(parents=True, exist_ok=True)
    rows = build_stimulus()

    # assert the extracted A:B <- P feedback is the identity concatenation
    d = DUMP.read_text()
    sec = re.search(r"==== CELL " + re.escape(ACC0) + r" ====\n(.*?)==== END CELL",
                    d, re.S).group(1)
    net2p = {m.group(2): int(m.group(1)) for m in
             re.finditer(r"^PIN P\[(\d+)\] dir=OUT net=(\S*) ", sec, re.M)}
    checked = 0
    for base, off in (("A", 18), ("B", 0)):
        for m in re.finditer(rf"^PIN {base}\[(\d+)\] dir=IN net=(\S*) ", sec, re.M):
            idx, net = int(m.group(1)), m.group(2)
            if net in net2p:
                assert net2p[net] == idx + off, \
                    f"feedback map not identity: {base}[{idx}]<-P[{net2p[net]}]"
                checked += 1
    print(f"feedback identity map verified on {checked} bits")

    for mode in (0, 1, 2):
        with open(DSIM / f"expected_mode{mode}.txt", "w") as f:
            for p2, p1 in golden(rows, mode):
                f.write(f"{p2:012x} {p1:012x}\n")
    with open(DSIM / "stim.txt", "w") as f:
        for cea, rsta2, a2, c2, tap, ce1, rst1 in rows:
            f.write(f"{cea} {rsta2} {a2} {c2 & MASK48:012x} {tap} {ce1} {rst1}\n")
    (DSIM / "dsp_dpreg_tb.v").write_text(TB)

    rc = run_local()
    if rc != 0:
        print(f"REFUSED: local sim run failed rc={rc}")
        return 1

    ok = True
    for line in (DSIM / "MANIFEST.sha256").read_text().splitlines():
        h, name = line.split(None, 1)
        name = name.strip().lstrip("*")
        lp = DSIM / name
        if lp.exists() and hashlib.sha256(lp.read_bytes()).hexdigest() != h:
            print(f"REFUSED: hash mismatch {name}")
            ok = False
    if not ok:
        return 1

    # ---- classify: X is a distinct failure class from wrong value ----
    verdict = {}
    exp = {m: (DSIM / f"expected_mode{m}.txt").read_text().splitlines()
           for m in (0, 1, 2)}
    for mode in (1, 2, 0):
        ap = DSIM / f"actual_mode{mode}.txt"
        rec = {"cycles": 0, "x_cycles": 0, "wrong_cycles": 0,
               "sim_aborted": not ap.exists()}
        first = None
        if ap.exists():
            lines = ap.read_text().splitlines()
            rec["cycles"] = len(lines)
            for i, line in enumerate(lines):
                p2h, p1h, xf = line.split()
                if xf == "1":
                    rec["x_cycles"] += 1
                elif i < len(exp[mode]) and f"{p2h} {p1h}" != exp[mode][i]:
                    rec["wrong_cycles"] += 1
                    if first is None:
                        first = {"cycle": i, "expected": exp[mode][i],
                                 "actual": f"{p2h} {p1h}"}
        rec["first_mismatch"] = first
        verdict[mode] = rec
    vlog2 = DSIM / "vvp_mode2.log"
    loop_evidence = vlog2.exists() and re.search(
        r"iteration limit|INFINITE|stopped|abort", vlog2.read_text(),
        re.I) is not None
    red1 = verdict[1]["wrong_cycles"] > 0
    red2 = (verdict[2]["x_cycles"] > 0 or verdict[2]["sim_aborted"])
    clean0 = (not verdict[0]["sim_aborted"] and
              verdict[0]["x_cycles"] == 0 and verdict[0]["wrong_cycles"] == 0)
    iver = subprocess.run(["iverilog", "-V"], capture_output=True, text=True)
    version_line = iver.stdout.splitlines()[0] if iver.stdout else "unknown"
    commit = (EVIDENCE / "unisim_source_commit.txt").read_text().split()[0]
    print(json.dumps(verdict, indent=2))
    print(f"pregflip control red : {'yes' if red1 else 'NO'} "
          f"(wrong={verdict[1]['wrong_cycles']}, x={verdict[1]['x_cycles']})")
    print(f"opmode-p control red : {'yes' if red2 else 'NO'} "
          f"(x={verdict[2]['x_cycles']}, aborted={verdict[2]['sim_aborted']}, "
          f"loop-evidence-in-log={loop_evidence})")
    print(f"clean sequence       : {'PASS' if clean0 else 'NO'} "
          f"(x={verdict[0]['x_cycles']}, wrong={verdict[0]['wrong_cycles']}, "
          f"cycles={verdict[0]['cycles']})")
    (DSIM / "verdict.json").write_text(json.dumps(
        {"verdict": verdict, "pregflip_red": red1, "opmodep_red": red2,
         "loop_evidence_in_log": loop_evidence, "clean_pass": clean0,
         "stimulus_rows": len(rows), "simulator": version_line,
         "unisim_commit": commit,
         "run_order": "controls (1=pregflip, 2=opmodep) before clean (0)"},
        indent=2))
    return 0 if (red1 and red2 and clean0) else 2


if __name__ == "__main__":
    sys.exit(main())
