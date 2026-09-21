module osc_2x_saw_bank(
 input wire clk,input wire rst_n,input wire frame_valid,input wire [1:0] select,
 input wire [23:0] phase0,phase1,phase2,input wire [23:0] inc0,inc1,inc2,
 input wire rectangular,input wire [23:0] duty,
 input wire [4:0] sh0,sh1,sh2,input wire [15:0] r0,r1,r2,
 output wire out_valid,output wire signed [15:0] out_sample);
 wire v0,v1,v2; wire signed [15:0] y0,y1,y2;
 osc_2x_saw_path p0(.clk(clk),.rst_n(rst_n),.rectangular(rectangular),.duty(duty),.frame_valid(frame_valid&&(select==0)),.phase(phase0),.inc(inc0),.sh(sh0),.recip(r0),.out_valid(v0),.out_sample(y0));
 osc_2x_saw_path p1(.clk(clk),.rst_n(rst_n),.rectangular(rectangular),.duty(duty),.frame_valid(frame_valid&&(select==1)),.phase(phase1),.inc(inc1),.sh(sh1),.recip(r1),.out_valid(v1),.out_sample(y1));
 osc_2x_saw_path p2(.clk(clk),.rst_n(rst_n),.rectangular(rectangular),.duty(duty),.frame_valid(frame_valid&&(select==2)),.phase(phase2),.inc(inc2),.sh(sh2),.recip(r2),.out_valid(v2),.out_sample(y2));
 assign out_valid = (select==0)?v0:(select==1)?v1:v2;
 assign out_sample = (select==0)?y0:(select==1)?y1:y2;
endmodule
