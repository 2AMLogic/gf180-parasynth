`timescale 1ns/1ps
`default_nettype none
module tb_rate_conv_2x;
    reg clk = 1'b0;
    always #5 clk = ~clk;
    reg rst_n = 1'b0;
    reg interp_valid = 1'b0;
    reg decim_valid = 1'b0;
    reg signed [15:0] x_in = 0;
    wire signed [16:0] x_even, x_odd;
    reg signed [18:0] y_even = 0, y_odd = 0;
    wire signed [18:0] y_out;
    rate_conv_2x dut(.*);

    integer fd, rc, frames = 0, mismatches = 0;
    integer xv, exp_even, exp_odd, in_y0, in_y1, exp_y;
    reg [1023:0] vector_path;

    initial begin
        if (!$value$plusargs("vectors=%s", vector_path)) begin
            $display("tb_rate_conv_2x: missing +vectors=<file>");
            $fatal(2);
        end
        fd = $fopen(vector_path, "r");
        if (!fd) begin
            $display("tb_rate_conv_2x: cannot open vectors");
            $fatal(2);
        end
        repeat (2) @(posedge clk);
        @(negedge clk); rst_n = 1'b1;
        while (!$feof(fd)) begin
            rc = $fscanf(fd, "%d %d %d %d %d %d\n",
                          xv, exp_even, exp_odd, in_y0, in_y1, exp_y);
            if (rc == 6) begin
                @(negedge clk);
                x_in = xv[15:0]; y_even = in_y0[18:0]; y_odd = in_y1[18:0];
                interp_valid = 1'b1; decim_valid = 1'b1;
                #1;
                if (x_even !== exp_even[16:0] || x_odd !== exp_odd[16:0]
                        || y_out !== exp_y[18:0]) begin
                    if (mismatches < 8)
                        $display("mismatch frame %0d: x=(%0d,%0d)/(%0d,%0d), y=%0d/%0d",
                                 frames, x_even, x_odd, exp_even, exp_odd, y_out, exp_y);
                    mismatches = mismatches + 1;
                end
                @(posedge clk);
                frames = frames + 1;
            end else if (rc != -1) begin
                $display("tb_rate_conv_2x: malformed vector row %0d (%0d fields)", frames, rc);
                $fatal(2);
            end
        end
        $fclose(fd);
        if (mismatches != 0) begin
            $display("RATE_CONV_FAIL frames=%0d mismatches=%0d", frames, mismatches);
            $fatal(1);
        end
        $display("RATE_CONV_PASS frames=%0d; interpolation and 19-bit decimation bit-exact", frames);
        $finish;
    end
endmodule
`default_nettype wire
