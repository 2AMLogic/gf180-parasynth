module test_decimate_2x_tm_sym_values;
 reg clk=0,rst_n=0,in_valid=0; reg signed [15:0] in_sample; wire out_valid; wire signed [15:0] out_sample;
 decimate_2x_tm_sym d(.*); always #5 clk=~clk; integer i;
 initial begin in_sample=0; repeat(2)@(posedge clk);rst_n=1;
  @(negedge clk);in_valid=1;in_sample=0;@(negedge clk);in_valid=0;
  repeat(2)@(posedge clk);
  @(negedge clk);in_valid=1;in_sample=-32693;@(negedge clk);in_valid=0;
  repeat(20)begin @(posedge clk);if(out_valid)$display("D %0d",out_sample);end $finish;end
endmodule
