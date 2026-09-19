`timescale 1ns/1ps
module test_decimate_2x;
    reg clk=0, rst_n=0, in_valid=0;
    reg signed [15:0] in_sample;
    wire out_valid; wire signed [15:0] out_sample;
    decimate_2x dut(.*);
    always #5 clk = ~clk;
    integer i, count;
    integer expected [0:7];
    initial begin
        count=0; in_sample=0;
        expected[0]=38; expected[1]=-44; expected[2]=33; expected[3]=71;
        expected[4]=-397; expected[5]=1132; expected[6]=-2819; expected[7]=10174;
        repeat (2) @(posedge clk); rst_n=1;
        for (i=0; i<128; i=i+1) begin
            @(negedge clk); in_valid=1; in_sample=i*97-4000;
            @(negedge clk); in_valid=0;
            @(posedge clk); if (out_valid) count=count+1;
        end
        if (count != 64) $fatal(1, "expected 64 outputs, got %0d", count);
        // Impulse response: odd output phases expose taps 1,3,...,15.
        rst_n=0; @(posedge clk); rst_n=1; count=0;
        for (i=0; i<32; i=i+1) begin
            @(negedge clk); in_valid=1; in_sample=(i == 0) ? 32767 : 0;
            @(negedge clk); in_valid=0;
            @(posedge clk); if (out_valid) begin
                if (count < 8 && out_sample !== expected[count])
                    $fatal(1, "tap %0d: expected %0d got %0d", count, expected[count], out_sample);
                count=count+1;
            end
        end
        if (count != 16) $fatal(1, "expected 16 impulse outputs, got %0d", count);
        $display("PASS decimate_2x outputs=%0d", count);
        $finish;
    end
endmodule
