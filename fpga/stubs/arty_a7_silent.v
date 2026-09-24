// Start-red wrapper: exposes clocks and the observation hierarchy, but no audio.
module arty_a7_top #(parameter SIM_NO_MMCM=0, parameter POR_BITS=12)(
    input wire clk_100mhz, input wire btn_reset, output wire [3:0] led,
    input wire spi_sck, spi_mosi, spi_cs_n, output wire spi_miso,
    output wire i2s_bclk, i2s_lrclk, i2s_sdata
);
    wire core_rst_n = ~btn_reset;
    assign led = 0;
    assign i2s_sdata = 0;
    synth_top u_synth(.clk(clk_100mhz), .rst_n_pad(core_rst_n),
        .sck(spi_sck), .mosi(spi_mosi), .cs_n(spi_cs_n), .miso(spi_miso),
        .bclk(i2s_bclk), .lrclk(i2s_lrclk), .sdata());
endmodule
