module test_polyblep_saw_pair;
 reg [23:0] phase=0,inc=38448; reg [4:0] sh=0; reg [15:0] recip=55854;
 wire signed [15:0] sample0,sample1;
 polyblep_saw_pair d(.phase(phase),.inc(inc),.sh(sh),.recip(recip),.sample0(sample0),.sample1(sample1));
 initial begin #1; $display("PAIR %0d %0d",sample0,sample1); $finish; end
endmodule
