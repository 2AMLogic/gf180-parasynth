// tb_sd_dac.v -- the no-DAC output's wire path: rtl-sketch/i2s_tx.v (the
// chip's transmitter) -> i2s_rx -> sd_dac, driven from a sample file, one
// sample per 256-clock frame strobed at cycle STROBE as the core does. The
// bench writes the modulator's bitstream, one character per clock, and nothing
// it computed about it: fpga/verify_sd_dac.py decimates that stream and
// compares it against the source file.
//
//   +samples=<hex, one 16-bit word per line>  +frames=<n>  +bits=<out file>
`timescale 1ns/1ps
`default_nettype none
module tb_sd_dac;
    localparam integer STROBE = 176;     // where synth_top's sample strobe lands
    reg clk = 0, rst_n = 0;
    always #40.690 clk = ~clk;           // 12.288 MHz
    reg [7:0] cyc = 0;
    reg sample_valid = 0;
    reg [15:0] sample = 0;
    reg [15:0] mem [0:1048575];
    integer frames, frame, fd;
    reg [8*512-1:0] samples_path, bits_path;
    wire bclk, lrclk, sdata, pdm;
    wire signed [15:0] rx_sample;
    i2s_tx u_tx (.clk(clk), .rst_n(rst_n), .cyc(cyc), .sample_valid(sample_valid),
                 .sample(sample), .bclk(bclk), .lrclk(lrclk), .sdata(sdata));
    i2s_rx u_rx (.clk(clk), .rst_n(rst_n), .bclk(bclk), .lrclk(lrclk), .sdata(sdata),
                 .sample(rx_sample));
    sd_dac u_sd (.clk(clk), .rst_n(rst_n), .sample(rx_sample), .pdm(pdm));
    initial begin
        if (!$value$plusargs("samples=%s", samples_path) || !$value$plusargs("frames=%d", frames)
                || !$value$plusargs("bits=%s", bits_path))
            $fatal(1, "usage: +samples= +frames= +bits=");
        $readmemh(samples_path, mem, 0, frames - 1);
        fd = $fopen(bits_path, "w");
        if (fd == 0) $fatal(1, "cannot open bits file");
        repeat (4) @(posedge clk);
        rst_n <= 1;
        for (frame = 0; frame < frames + 2; frame = frame + 1) begin
            repeat (256) begin
                @(posedge clk);
                cyc <= cyc + 8'd1;
                sample_valid <= (cyc == STROBE - 1) && frame < frames;
                if (cyc == STROBE - 1 && frame < frames) sample <= mem[frame];
                $fwrite(fd, "%b", pdm);
            end
        end
        $fclose(fd);
        $display("TB_SD_DAC DONE frames=%0d cycles=%0d", frames, (frames + 2) * 256);
        $finish;
    end
endmodule
`default_nettype wire
