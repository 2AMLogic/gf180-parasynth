// SPDX-License-Identifier: Apache-2.0
//
// chip_core -- the wafer.space gf180mcu project-template core, carrying synth_top.
//
// The template's chip_top.sv owns the padframe; this module is the only thing a
// project is meant to replace. It is a PIN MAP and nothing else: it instantiates
// `synth_top` unchanged (same RTL files pnr/orfs/synth_top/config.mk uses, read
// from rtl-sketch/ in place so the $readmemh table paths still resolve) and wires
// its nine signals to pads.
//
// PIN MAP (half-height 1x0.5 slot; pad ORDER on each edge is fixed by
// librelane/slots/slot_1x0p5.yaml, which is the template's file, unedited):
//
//   pad              edge   direction  net
//   ---------------- -----  ---------  ---------------------------------------
//   clk_PAD          SOUTH  in (in_s)  clk            12.288 MHz, Schmitt trigger
//   rst_n_PAD        SOUTH  in (in_c)  rst_n_pad      active low, synchronised inside synth_top
//   bidir_PAD[0]     SOUTH  in         sck            SPI
//   bidir_PAD[1]     SOUTH  in         mosi           SPI
//   bidir_PAD[2]     SOUTH  in         cs_n           SPI
//   bidir_PAD[3]     SOUTH  out        miso           SPI
//   bidir_PAD[4]     SOUTH  out        bclk           I2S
//   bidir_PAD[5]     SOUTH  out        lrclk          I2S
//   bidir_PAD[6]     SOUTH  out        sdata          I2S
//   bidir_PAD[7]     SOUTH  in         uart_rxd       UART bridge (WITH_UART=0 here)
//   bidir_PAD[8]     SOUTH  out        uart_txd       UART bridge (idles high)
//
// RECONCILIATION, 2026-09-26 (#33). This file was written against a synth_top
// that had nine ports. `main` has since added the UART bridge's two pins
// (rtl-sketch/synth_top.v, commit bcde9e6, `uart_rxd` / `uart_txd`), so the
// wrapper is ELEVEN signals now, not nine, and the FPGA build's nine-signal
// figure no longer describes this wrapper. The two pins are brought out rather
// than tied off inside the wrapper for one reason that matters to this flow: the
// ports exist on the module either way, so tying them off would hide a real
// port-list drift behind an internal constant, and the next revision that turns
// WITH_UART on would need a pad change nobody had budgeted. WITH_UART defaults
// to 0, so `uart_bridge` is not elaborated and synth_top drives uart_txd = 1'b1
// (its `g_no_uart` branch) -- an idle UART line, which is the correct level.
//
// Everything else the template supplies is left in a defined state, not left to
// chance:
//
//   bidir_PAD[45:9]  37 unused bidirectional pads: output driver off (OE=0) AND
//                    input receiver off (IE=0), with the weak pull-down on (PD=1).
//                    A disabled receiver cannot draw crowbar current from a floating
//                    pin; the pull-down additionally gives the pin a defined level on
//                    the breakout PCB.
//   input_PAD[3:0]   4 unused input-only pads. gf180mcu_fd_io__in_c has NO input
//                    enable -- its receiver is always live -- so these MUST be
//                    pulled down or they float into a live input stage.
//   analog_PAD[3:0]  4 analog pass-through pads, unconnected (asig_5p0 is passive).
//
// Pad option bits on the nine live signals: the three SPI inputs and the UART
// receive pin take the Schmitt receiver (CS=1) because they arrive over PCB wiring
// from a host MCU; the five outputs use the template's default fast slew (SL=0).

`default_nettype none

module chip_core #(
    parameter NUM_INPUT_PADS,
    parameter NUM_BIDIR_PADS,
    parameter NUM_ANALOG_PADS
    )(
    `ifdef USE_POWER_PINS
    inout  wire VDD,
    inout  wire VSS,
    `endif

    input  wire clk,
    input  wire rst_n,

    input  wire [NUM_INPUT_PADS-1:0] input_in,
    output wire [NUM_INPUT_PADS-1:0] input_pu,
    output wire [NUM_INPUT_PADS-1:0] input_pd,

    input  wire [NUM_BIDIR_PADS-1:0] bidir_in,
    output wire [NUM_BIDIR_PADS-1:0] bidir_out,
    output wire [NUM_BIDIR_PADS-1:0] bidir_oe,
    output wire [NUM_BIDIR_PADS-1:0] bidir_cs,
    output wire [NUM_BIDIR_PADS-1:0] bidir_sl,
    output wire [NUM_BIDIR_PADS-1:0] bidir_ie,
    output wire [NUM_BIDIR_PADS-1:0] bidir_pu,
    output wire [NUM_BIDIR_PADS-1:0] bidir_pd,

    inout  wire [NUM_ANALOG_PADS-1:0] analog
);

    // ---- pad index map -------------------------------------------------------
    localparam integer P_SCK   = 0;
    localparam integer P_MOSI  = 1;
    localparam integer P_CSN   = 2;
    localparam integer P_MISO  = 3;
    localparam integer P_BCLK  = 4;
    localparam integer P_LRCLK = 5;
    localparam integer P_SDATA = 6;
    localparam integer P_URXD  = 7;
    localparam integer P_UTXD  = 8;
    localparam integer N_USED  = 9;

    wire miso, bclk, lrclk, sdata, uart_txd;

    // ---- the chip ------------------------------------------------------------
    // Every port of synth_top is listed. A positional or partial connection here
    // would let a future port addition bind silently to nothing, which is exactly
    // the drift this file just had to be reconciled for.
    synth_top u_synth (
        .clk        (clk),
        .rst_n_pad  (rst_n),
        .sck        (bidir_in[P_SCK]),
        .mosi       (bidir_in[P_MOSI]),
        .cs_n       (bidir_in[P_CSN]),
        .miso       (miso),
        .uart_rxd   (bidir_in[P_URXD]),
        .uart_txd   (uart_txd),
        .bclk       (bclk),
        .lrclk      (lrclk),
        .sdata      (sdata)
    );

    // ---- pad direction / option bits ----------------------------------------
    // Outputs drive; inputs receive; every unused pad is off in both directions.
    wire [NUM_BIDIR_PADS-1:0] is_out;
    wire [NUM_BIDIR_PADS-1:0] is_in;
    assign is_out = (1 << P_MISO) | (1 << P_BCLK) | (1 << P_LRCLK) | (1 << P_SDATA)
                  | (1 << P_UTXD);
    assign is_in  = (1 << P_SCK)  | (1 << P_MOSI) | (1 << P_CSN) | (1 << P_URXD);

    assign bidir_oe = is_out;
    assign bidir_ie = is_in;
    assign bidir_cs = is_in;                              // Schmitt receiver on the SPI/UART inputs
    assign bidir_sl = {NUM_BIDIR_PADS{1'b0}};             // fast slew (template default)
    assign bidir_pu = {NUM_BIDIR_PADS{1'b0}};
    assign bidir_pd = ~(is_out | is_in);                  // pull down the 37 unused pads

    assign bidir_out[P_MISO]  = miso;
    assign bidir_out[P_BCLK]  = bclk;
    assign bidir_out[P_LRCLK] = lrclk;
    assign bidir_out[P_SDATA] = sdata;
    assign bidir_out[P_UTXD]  = uart_txd;
    assign bidir_out[P_SCK]   = 1'b0;
    assign bidir_out[P_MOSI]  = 1'b0;
    assign bidir_out[P_CSN]   = 1'b0;
    assign bidir_out[P_URXD]  = 1'b0;
    assign bidir_out[NUM_BIDIR_PADS-1:N_USED] = {(NUM_BIDIR_PADS-N_USED){1'b0}};

    // in_c has no input enable: its receiver is always on, so an unused one is
    // pulled down rather than left floating.
    assign input_pu = {NUM_INPUT_PADS{1'b0}};
    assign input_pd = {NUM_INPUT_PADS{1'b1}};

    // Tie off the reads this design does not use, so lint sees them consumed.
    wire _unused;
    assign _unused = &{1'b0, bidir_in[NUM_BIDIR_PADS-1:N_USED], input_in, 1'b0};

endmodule

`default_nettype wire
