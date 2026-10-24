# Upstream report (READY TO PASTE, NOT YET FILED): Icarus Verilog `generate case` over a string parameter selects nothing under `-g2012`

Target: <https://github.com/steveicarus/iverilog/issues/new>
Status: **awaiting an operator to file.** Nobody has authorised publishing from
the headless session that wrote this. After filing, record the issue URL here
and in `fpga/reports/arty/vivado-2025.1/dsp-dpreg-disposition.md` section 7
item 1. Tracked in 2AMLogic/gf180-parasynth#608.

Duplicate search (2026-10-09, GitHub issue search on steveicarus/iverilog for
"generate case string parameter"): no matching issue found. This was a quick
search through the GitHub API, not an exhaustive one; repeat it when filing.

---

## Title

`generate case (STRING_PARAM)` elaborates no items with `-g2012` ("No generate items found"), correct with `-g2005`

## Body

**Version**

```
$ iverilog -V | head -1
Icarus Verilog version 13.0 (stable) (v13_0)
```

Linux x86_64 (Ubuntu, kernel 7.0.0-1013-aws), build from the OSS CAD pinned
toolchain (`~/.cache/icarus-13.0`).

**Summary**

A `case` generate construct whose selector is a string parameter and whose
items are string literals selects no branch under `-g2012`, so the generated
logic is absent and its output stays `x`. The same source under `-g2005`
elaborates correctly. This pattern appears in vendor simulation models (for
example Xilinx unisim `DSP48E1`, `B_INPUT = "DIRECT"|"CASCADE"`), so with
`-g2012` such models silently simulate with missing logic.

**Reproducer** (`micro.v`)

```verilog
module micro #(
    parameter B_INPUT = "DIRECT",
    parameter W = 8
)(
    input wire [W-1:0] din,
    output reg  [W-1:0] dout
);
    generate
       case (B_INPUT)
          "DIRECT"  : always @(din) dout <= din;
          "CASCADE" : always @(din) dout <= ~din;
       endcase
    endgenerate
endmodule
module tb;
    reg [7:0] d = 8'h5A;
    wire [7:0] q;
    micro #(.B_INPUT("DIRECT")) u (.din(d), .dout(q));
    initial begin #1 $display("q=%h (expect 5a)", q); $finish; end
endmodule
```

**Commands and observed output**

```
$ iverilog -g2005 -o m2005.vvp micro.v && vvp m2005.vvp
q=5a (expect 5a)
micro.v:19: $finish called at 1 (1s)

$ iverilog -g2012 -o m2012.vvp micro.v && vvp m2012.vvp
q=xx (expect 5a)
micro.v:19: $finish called at 1 (1s)
```

Both compile without warnings or errors. Expected: `q=5a` under both
standards. Actual: `q=xx` under `-g2012`. Elaboration debug output for the
`-g2012` run notes "No generate items found" for the case construct (the
original investigation recorded this with the elaboration debug flags; this
re-run reproduced the `q=xx` outcome and did not re-capture that note).

**Workaround**: compile with `-g2005`.

**Evidence in the downstream project**: the committed repro and the
three-mode vendor-model comparison are under
`fpga/reports/arty/vivado-2025.1/dsp-dpreg-evidence/dsim/` in
2AMLogic/gf180-parasynth.
