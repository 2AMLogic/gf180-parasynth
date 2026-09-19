`timescale 1ns/1ps
module test_decimate_2x;
    reg clk=0, rst_n=0, in_valid=0;
    reg signed [15:0] in_sample;
    wire out_valid; wire signed [15:0] out_sample;
    decimate_2x dut(.*);
    always #5 clk = ~clk;
    integer i, count;
    initial begin
        count=0; in_sample=0;
        repeat (2) @(posedge clk); rst_n=1;
        for (i=0; i<128; i=i+1) begin
            @(negedge clk); in_valid=1; in_sample=i*97-4000;
            @(negedge clk); in_valid=0;
            @(posedge clk); if (out_valid) count=count+1;
        end
        if (count != 64) $fatal(1, "expected 64 outputs, got %0d", count);
        $display("PASS decimate_2x outputs=%0d", count);
        $finish;
    end
endmodule
