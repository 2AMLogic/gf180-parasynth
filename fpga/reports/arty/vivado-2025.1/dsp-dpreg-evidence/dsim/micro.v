module micro #(
    parameter B_INPUT = "DIRECT",
    parameter W = 8
)(
    input wire [W-1:0] din,
    output reg  [W-1:0] dout
);
    generate
       case (B_INPUT)
          "DIRECT"  : always @(din) dout <= din;
          "CASCADE" : always @(din) dout <= ~din;
       endcase
    endgenerate
endmodule
module tb;
    reg [7:0] d = 8'h5A;
    wire [7:0] q;
    micro #(.B_INPUT("DIRECT")) u (.din(d), .dout(q));
    initial begin #1 $display("q=%h (expect 5a)", q); $finish; end
endmodule
