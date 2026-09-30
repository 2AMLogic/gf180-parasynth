// tanh_dp.v -- the smallest datapath that reproduces `ladder_dp_t16`'s shipped
// defect, for the X-propagation control in `pnr/report_synth_area.py`.
//
// THIS FILE IS A FIXTURE, NOT A DESIGN. Nothing in the chip instantiates it and
// nothing should: it exists so that the tool which reports an area can be shown
// to REFUSE on a netlist whose outputs are X, on a design that synthesises in
// seconds rather than the minutes `ladder_dp` needs.
//
// What it reproduces, from `docs/verification-rules.md` rule 1: "its `tanh`
// index was out of range, yosys marked the datapath don't-care, every output was
// X -- and it was quoted at 1,917 cells for three rounds". The shape is the
// whole point:
//
//   * a (2^n + 1)-word tanh ROM read at bin edges, exactly as `ladder_dp` reads
//     `rom[a[21:22-n]]`, with the guard word at index 2^n;
//   * an index field whose WIDTH is a parameter. IDX_BITS = TANH_LOG2N is the
//     table's own index width and is correct. IDX_BITS = TANH_LOG2N + 1 is the
//     shipped bug: one bit too wide, so `idx` reaches 31 on a 17-word table and
//     the read is out of range, which is `x` in Verilog and don't-care to yosys;
//   * an accumulator, so the x is STICKY -- once the datapath is poisoned it
//     stays poisoned, which is why the real failure showed every output X
//     rather than an occasional x;
//   * a synchronous reset on every register, so an x at an output can only have
//     come from the read and not from an uninitialised flop. That precondition
//     is what makes the dynamic half of the control readable at all.
//
// The clamp above 4.0 does NOT mask the bug, and that is deliberate: `clamp`
// reads a[SW-1:22] while `idx` reads a[21 -: IDX_BITS], so a widened index goes
// out of range for inputs the clamp never sees -- which is how the original
// survived a reviewer's eye.
`default_nettype none
module tanh_dp #(
    parameter SW         = 24,           // state width, Q4.20, as LadderFx
    parameter TANH_LOG2N = 4,            // 2^n table entries over [0, 4)
    parameter IDX_BITS   = 4,            // index field width; 4 is correct here
    parameter ROM_FILE   = "tanh16.hex"  // 2^n + 1 words, rtl-sketch/tanh_rom.py
)(
    input  wire                 clk,
    input  wire                 rst_n,
    input  wire                 valid,
    input  wire signed [SW-1:0] v,
    input  wire        [15:0]   g,
    output reg  signed [16:0]   y,
    output reg                  y_valid
);
    localparam integer N = 1 << TANH_LOG2N;

    reg signed [15:0] rom [0:N];         // N + 1 words: the model's guard word
    initial $readmemh(ROM_FILE, rom);

    wire [SW-1:0]         a     = v[SW-1] ? -v : v;
    wire                  clamp = |a[SW-1:22];          // |v| >= 4.0
    wire [IDX_BITS-1:0]   idx   = a[21 -: IDX_BITS];
    wire signed [15:0]    t     = clamp ? rom[N] : rom[idx];

    reg signed [SW-1:0] acc;
    wire signed [SW+15:0] step = $signed(t) * $signed({1'b0, g});
    wire signed [SW:0]    sum  = acc + step[SW+15:16];

    always @(posedge clk) begin
        if (!rst_n) begin
            acc     <= {SW{1'b0}};
            y       <= 17'sd0;
            y_valid <= 1'b0;
        end else if (valid) begin
            acc     <= (sum > $signed({1'b0, {(SW-1){1'b1}}})) ? $signed({1'b0, {(SW-1){1'b1}}})
                     : (sum < $signed({1'b1, {(SW-1){1'b0}}})) ? $signed({1'b1, {(SW-1){1'b0}}})
                     : sum[SW-1:0];
            y       <= acc[SW-1:SW-17];
            y_valid <= 1'b1;
        end
    end
endmodule
`default_nettype wire
