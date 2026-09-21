`timescale 1ns/1ps
module test_osc_substep_pair;
    reg clk=0, rst_n=0, frame_valid=0;
    reg signed [15:0] sample0, sample1;
    wire out_valid; wire signed [15:0] out_sample;
    osc_substep_pair dut(.*);
    always #5 clk=~clk;
    integer i, count;
    initial begin
        sample0=0; sample1=0; count=0;
        repeat (2) @(posedge clk); rst_n=1;
        for (i=0; i<8; i=i+1) begin
            @(negedge clk); frame_valid=1; sample0=i*100; sample1=i*100+50;
            @(negedge clk); frame_valid=0;
            repeat (40) begin @(posedge clk); if (out_valid) count=count+1; end
        end
        if (count != 8) $fatal(1, "expected 8 filtered frames, got %0d", count);
        $display("PASS osc_substep_pair frames=%0d", count);
        $finish;
    end
endmodule
