// One base-rate oscillator frame: accept two 2x samples, return one filtered
// sample. The caller owns PolyBLEP generation and phase advancement.
module osc_substep_pair(
    input  wire               clk,
    input  wire               rst_n,
    input  wire               frame_valid,
    input  wire signed [15:0] sample0,
    input  wire signed [15:0] sample1,
    output reg                out_valid,
    output reg signed [15:0] out_sample
);
    reg in_valid;
    reg signed [15:0] in_sample;
    reg busy, second_sent;
    wire dec_valid;
    wire signed [15:0] dec_sample;
    decimate_2x_tm_sym dec(.clk(clk), .rst_n(rst_n), .in_valid(in_valid),
                    .in_sample(in_sample), .out_valid(dec_valid),
                    .out_sample(dec_sample));
    always @(posedge clk) begin
        in_valid <= 1'b0;
        out_valid <= 1'b0;
        if (!rst_n) begin
            busy <= 1'b0;
            second_sent <= 1'b0;
            in_sample <= 0;
            out_sample <= 0;
        end else if (dec_valid) begin
            out_sample <= dec_sample;
            out_valid <= 1'b1;
            busy <= 1'b0;
        end else if (frame_valid && !busy) begin
            in_sample <= sample0;
            in_valid <= 1'b1;
            busy <= 1'b1;
            second_sent <= 1'b0;
        end else if (busy && !second_sent) begin
            in_sample <= sample1;
            in_valid <= 1'b1;
            second_sent <= 1'b1;
        end
    end
endmodule
