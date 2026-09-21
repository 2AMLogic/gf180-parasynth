module test_polyblep_saw_pair;
 reg [23:0] phase=0,inc=38448; reg [4:0] sh=0; reg [15:0] recip=55854;
 wire signed [15:0] sample0,sample1;
 polyblep_saw_pair d(.rectangular(1'b0),.duty(24'd0),.phase(phase),.inc(inc),.sh(sh),.recip(recip),.sample0(sample0),.sample1(sample1));
 integer i; initial begin
  for(i=0;i<8;i=i+1) begin phase=i*38448; #1; $display("PAIR %0d %0d",sample0,sample1); end
  $finish;
 end
endmodule
