// Symmetric time-multiplexed 31-tap decimator: 15 paired products plus the
// centre tap, one MAC per cycle (16 cycles per output).
module decimate_2x_tm_sym(
 input wire clk,input wire rst_n,input wire in_valid,input wire signed [15:0] in_sample,
 output reg out_valid,output reg signed [15:0] out_sample);
 reg signed [15:0] h[0:15],x[0:30]; reg parity,busy; reg [4:0] tap;
 reg signed [47:0] acc; reg signed [32:0] prod; reg signed [16:0] pair_sum; integer i;
 initial begin
  h[0]=39;h[1]=54;h[2]=-44;h[3]=-138;h[4]=34;h[5]=323;h[6]=72;h[7]=-609;
  h[8]=-397;h[9]=957;h[10]=1133;h[11]=-1296;h[12]=-2819;h[13]=1544;h[14]=10175;h[15]=14712;
 end
 always @(posedge clk) begin
  out_valid<=0;
  if(!rst_n) begin parity<=0;busy<=0;tap<=0;acc<=0;out_sample<=0;for(i=0;i<31;i=i+1)x[i]<=0; end
  else if(in_valid&&!busy) begin
   for(i=30;i>0;i=i-1)x[i]<=x[i-1]; x[0]<=in_sample;
   if(parity) begin busy<=1;tap<=0;acc<=0;end parity<=~parity;
  end else if(busy) begin
   if(tap<15) begin pair_sum=$signed(x[tap])+$signed(x[30-tap]); prod=h[tap]*pair_sum; end
   else prod=h[15]*x[15];
   if(tap==15) begin
    if($signed(acc+prod) >>> 15 > 32767) out_sample<=16'sd32767;
    else if($signed(acc+prod) >>> 15 < -32768) out_sample<=-16'sd32768;
    else out_sample<=(acc+prod)>>>15;
    out_valid<=1;busy<=0;
   end
   else begin acc<=acc+prod;tap<=tap+1'b1;end
  end
 end
endmodule
