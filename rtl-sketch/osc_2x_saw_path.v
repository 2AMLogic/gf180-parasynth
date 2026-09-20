// Complete standalone saw path: phase/increment -> two PolyBLEP samples ->
// time-multiplexed decimator. The voice FSM supplies frame_valid and advances
// phase only after out_valid.
module osc_2x_saw_path(
    input wire clk, input wire rst_n, input wire frame_valid,
    input wire [23:0] phase, input wire [23:0] inc,
    input wire [4:0] sh, input wire [15:0] recip,
    output wire out_valid, output wire signed [15:0] out_sample
);
    wire signed [15:0] sample0, sample1;
    polyblep_saw_pair gen(.phase(phase), .inc(inc), .sh(sh), .recip(recip),
                          .sample0(sample0), .sample1(sample1));
    osc_substep_pair pair(.clk(clk), .rst_n(rst_n), .frame_valid(frame_valid),
                          .sample0(sample0), .sample1(sample1),
                          .out_valid(out_valid), .out_sample(out_sample));
endmodule
