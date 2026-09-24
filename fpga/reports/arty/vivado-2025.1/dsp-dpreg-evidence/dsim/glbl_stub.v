`timescale 1ns/1ps
module glbl;
    reg GSR = 1'b1;
    initial begin #1; GSR = 1'b0; end
endmodule
