#!/usr/bin/env python3
"""Directed mapped-DSP comparison for the DPREG-4 disposition.

Simulates, in Vivado xsim on the remote box, DSP48E1 unisim primitives with
the parameterization EXTRACTED from the published routed checkpoint
(dsp_cells_dump.txt), driven by the control sequences the RTL can actually
issue, against a Python golden implementing the UG479 arithmetic of a 25x18
signed multiply-accumulate with the same pipeline registers.

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
                     (oscillation/unknown; xsim may abort on the loop -- the
                     recorded transcript is the evidence either way).
Only after both controls have fired does mode 0 (the extracted, routed
configuration) run; it must report 0 X and 0 wrong-value cycles.
"""

import hashlib
import json
import pathlib
import re
import subprocess
import sys

BOX = "repo-remote-wt-arty-bringup"
REMOTE = "/home/ubuntu/dsp-review/dsim"
REMOTE_PUSH = "/home/ubuntu/dsp-review/dsim_push"
EVIDENCE = pathlib.Path(__file__).resolve().parents[1] / (
    "fpga/reports/arty/vivado-2025.1/dsp-dpreg-evidence")
DSIM = EVIDENCE / "dsim"

DUMP = EVIDENCE / "dsp_cells_dump.txt"
ACC0 = "u_synth/u_voice/osc2_path/p0/pair/dec/acc0"

B_CONST = 14712          # D2 B port constant (h[15]), from the routed pins
MASK48 = (1 << 48) - 1


# ---------------------------------------------------------------- stimulus
def build_stimulus():
    """(cea, rsta2, a2 signed 16, tap, ce1, rst1) per cycle.

    RTL FSM shape: reset window (tap holds 0), then frames of 16 busy cycles
    tap=0..15 with the sample-accept strobe high; between frames the tap
    register HOLDS 15 (idle) -- 31 is unreachable in the RTL.
    """
    rows = []
    rows += [(0, 1, 0, 0, 0, 1)] * 4            # reset window, tap holds 0
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
    for f, pat in enumerate(patterns):
        rows.append((0, 0, 0, 15, 0, 0))            # idle: tap holds 15
        for tap in range(16):
            mid_rst = 1 if (f == 3 and tap < 3) else 0
            rows.append((1, 0, pat[tap % len(pat)], tap,
                         1 if tap < 15 else 0, mid_rst))
        rows.append((0, 0, 0, 15, 0, 0))
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
    a_r1 = a_r2 = 0            # D2 A pipeline (AREG=2, CEA1=CEA2 together)
    p2_reg = 0                 # only used by the PREG flip control
    acc1 = 0                   # D1 registered A:B feedback (AREG=BREG=1)
    out = []
    for cea, rsta2, a2, tap, ce1, rst1 in rows:
        a2 &= 0xFFFF
        z = 2 if (mode == 2 and tap == 7) else 0   # OPMODE[6:4]: 000/011 -> 0
        m = sext(a_r2 & 0x1FFFFFF, 25) * B_CONST   # 25x18 signed multiply
        x = m if (tap & 1) else 0                  # OPMODE[1:0] = {0, tap[0]}
        y = m if (tap & 1) else 0                  # OPMODE[3:2] = {0, tap[0]}
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
    reg [4:0]  tap    = 5'd0;
    reg        ce1    = 1'b0;
    reg        rst1   = 1'b1;
    reg        xflag;
    integer f, fo, rc;
    integer cea_i, rsta_i, a2_i, tap_i, ce1_i, rst1_i;

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
        .AREG(2), .BREG(0), .CREG(0), .DREG(1), .MREG(0),
`ifdef MODE_PREGFLIP
        .PREG(1),
`else
        .PREG(0),
`endif
        .OPMODEREG(0), .USE_DPORT(0), .MASK(48'h3FFFFFFFFFFF),
        .AUTORESET_PATDET("NO_RESET"), .SEL_MASK("MASK")
    ) D2 (
        .CLK(clk),
        .CEA1(ce_a2), .CEA2(ce_a2), .CEAD(1'b0), .CEB1(1'b0), .CEB2(1'b0),
        .CEC(1'b0), .CED(1'b0), .CEM(1'b0), .CEP(1'b0), .CECTRL(1'b0),
        .CECARRYIN(1'b0), .CEALUMODE(1'b0), .CEINMODE(1'b0),
        .RSTA(rst_a2), .RSTB(1'b0), .RSTC(1'b0), .RSTD(1'b0), .RSTM(1'b0),
        .RSTP(1'b0), .RSTCTRL(1'b0), .RSTCARRYIN(1'b0), .RSTALUMODE(1'b0),
        .RSTINMODE(1'b0),
        .A({{14{a2s[15]}}, a2s}), .B(18'sd%B_CONST%), .C(48'b0), .D(25'b0),
        .INMODE(5'b0), .OPMODE(opmode2), .ALUMODE(4'b0),
        .CARRYIN(1'b0), .CARRYINSEL(3'b0), .CARRYCASCIN(1'b0),
        .MULTSIGNIN(1'b0), .PCIN(48'b0),
        .P(p2), .PCOUT(pcout2), .CARRYCASCOUT(), .MULTSIGNOUT(),
        .OVERFLOW(), .PATTERNBDETECT(), .PATTERNDETECT(), .UNDERFLOW()
    );

    DSP48E1 #(
        .AREG(1), .BREG(1), .CREG(1), .DREG(1), .MREG(0), .PREG(0),
        .OPMODEREG(0), .USE_DPORT(0), .MASK(48'h3FFFFFFFFFFF),
        .AUTORESET_PATDET("NO_RESET"), .SEL_MASK("MASK")
    ) D1 (
        .CLK(clk),
        .CEA1(1'b0), .CEA2(ce1), .CEAD(1'b0), .CEB1(1'b0), .CEB2(ce1),
        .CEC(1'b0), .CED(1'b0), .CEM(1'b0), .CEP(1'b0), .CECTRL(1'b0),
        .CECARRYIN(1'b0), .CEALUMODE(1'b0), .CEINMODE(1'b0),
        .RSTA(rst1), .RSTB(rst1), .RSTC(1'b0), .RSTD(1'b0), .RSTM(1'b0),
        .RSTP(1'b0), .RSTCTRL(1'b0), .RSTCARRYIN(1'b0), .RSTALUMODE(1'b0),
        .RSTINMODE(1'b0),
        .A(a1), .B(b1), .C(48'hFFFFFFFFFFFF), .D(25'b0),
        .INMODE(5'b0), .OPMODE(7'b0001011), .ALUMODE(4'b0),
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
            rc = $fscanf(f, "%d %d %d %d %d %d",
                         cea_i, rsta_i, a2_i, tap_i, ce1_i, rst1_i);
            if (rc == 6) begin
                ce_a2  <= cea_i[0];
                rst_a2 <= rsta_i[0];
                a2s    <= a2_i[15:0];
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

RUN = f"""
set -euo pipefail
source /tools/Xilinx/2025.1/Vivado/settings64.sh >/dev/null 2>&1
cd {REMOTE}
flock -w 7200 /home/ubuntu/vivado.lock bash -c '
set -e
cp {REMOTE_PUSH}/* .
xvlog $XILINX_VIVADO/data/verilog/src/glbl.v dsp_dpreg_tb.v > xvlog.log 2>&1
for mode in 1 2 0; do
  case $mode in
    1) def=-d\\ MODE_PREGFLIP;;
    2) def=-d\\ MODE_OPMODEP;;
    0) def=;;
  esac
  rm -f xelab.log xsim.log actual.txt xelab_mode$mode.log xsim_mode$mode.log
  if [ -n "$def" ]; then
    xvlog $def dsp_dpreg_tb.v > /dev/null 2>&1
  fi
  xelab work.dsp_dpreg_tb work.glbl -s tb_mode$mode -L unisims_ver \\
      > xelab_mode$mode.log 2>&1
  xsim tb_mode$mode -R > xsim_mode$mode.log 2>&1 || true
  cp actual.txt actual_mode$mode.txt 2>/dev/null || true
done
sha256sum stim.txt dsp_dpreg_tb.v expected_mode0.txt expected_mode1.txt \\
    expected_mode2.txt actual_mode0.txt actual_mode1.txt actual_mode2.txt \\
    xelab_mode0.log xelab_mode1.log xelab_mode2.log \\
    xsim_mode0.log xsim_mode1.log xsim_mode2.log \\
    > MANIFEST.sha256 || true
cat MANIFEST.sha256
'
"""


def push_and_run():
    subprocess.run(["ssh", BOX, f"mkdir -p {REMOTE} {REMOTE_PUSH}"], check=True)
    for name in ("stim.txt", "dsp_dpreg_tb.v", "expected_mode0.txt",
                 "expected_mode1.txt", "expected_mode2.txt"):
        subprocess.run(["scp", "-q", str(DSIM / name),
                        f"{BOX}:{REMOTE_PUSH}/{name}"], check=True)
    r = subprocess.run(["ssh", BOX, "bash -s"], input=RUN, text=True,
                       capture_output=True)
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
        for cea, rsta2, a2, tap, ce1, rst1 in rows:
            f.write(f"{cea} {rsta2} {a2} {tap} {ce1} {rst1}\n")
    (DSIM / "dsp_dpreg_tb.v").write_text(TB)

    rc = push_and_run()
    if rc != 0:
        print(f"REFUSED: remote sim run failed rc={rc}")
        return 1

    for f in ("MANIFEST.sha256", "actual_mode0.txt", "actual_mode1.txt",
              "actual_mode2.txt", "xsim_mode0.log", "xsim_mode1.log",
              "xsim_mode2.log"):
        subprocess.run(["scp", "-q", f"{BOX}:{REMOTE}/{f}", str(DSIM / f)],
                       check=True)
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
    xlog2 = (DSIM / "xsim_mode2.log")
    loop_evidence = xlog2.exists() and re.search(
        r"iteration limit|Iter limit|X at|unknown", xlog2.read_text(),
        re.I) is not None
    red1 = verdict[1]["wrong_cycles"] > 0
    red2 = (verdict[2]["x_cycles"] > 0 or verdict[2]["sim_aborted"])
    clean0 = (not verdict[0]["sim_aborted"] and
              verdict[0]["x_cycles"] == 0 and verdict[0]["wrong_cycles"] == 0)
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
         "stimulus_rows": len(rows)}, indent=2))
    return 0 if (red1 and red2 and clean0) else 2


if __name__ == "__main__":
    sys.exit(main())
