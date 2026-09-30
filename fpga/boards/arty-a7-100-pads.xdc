# Arty A7-100 Rev D/E -- the PADS DEMO wrapper (fpga/rtl/arty_a7_pads_top.v, #449).
# A separate constraint file: fpga/boards/arty-a7-100.xdc (the release image's)
# is unchanged. Everything below the "pads inputs" block is that file's
# constraints for the same ports, restated so the demo image keeps the release
# image's link and audio timing.
#
# Pin source: Digilent master XDC at 00a3404901f35aa9567b01ecb3f2c233b6efe9f4
# https://github.com/Digilent/digilent-xdc/blob/00a3404901f35aa9567b01ecb3f2c233b6efe9f4/Arty-A7-100-Master.xdc
# (fetched 2026-09-28, sha256 5c0c84302cbce49ac85f4812e8f1f7371e686964ec2eb29fd67307da9ed6835f):
#   line 11  sw[0]  A8   IO_L12N_T1_MRCC_16
#   line 14  sw[3]  A10  IO_L14P_T2_SRCC_16
#   line 37  btn[0] D9   IO_L6N_T0_VREF_16
#   line 38  btn[1] C9   IO_L11P_T1_SRCC_16
#   line 39  btn[2] B9   IO_L11N_T1_SRCC_16
#   line 40  btn[3] B8   IO_L12P_T1_MRCC_16
# That is the vendor file, not the board in hand: a human confirms the
# silkscreen and the button polarity (pressed = 1) before this is trusted (#449).
set_property PACKAGE_PIN E3 [get_ports clk_100mhz]
set_property IOSTANDARD LVCMOS33 [get_ports clk_100mhz]
create_clock -name clk100 -period 10.000 [get_ports clk_100mhz]

# ---- pads inputs --------------------------------------------------------------
# BTN0..BTN3 play BD, SD, CH, CP (fpga/pads_rom.VOICES). BTN0 was the release
# image's reset; here it is a pad, and the reset is SW3 (a toggle, either way).
# SW0 selects the UART source: down = host (the FTDI's A9), up = pads.
set_property PACKAGE_PIN D9 [get_ports {btn[0]}]
set_property PACKAGE_PIN C9 [get_ports {btn[1]}]
set_property PACKAGE_PIN B9 [get_ports {btn[2]}]
set_property PACKAGE_PIN B8 [get_ports {btn[3]}]
set_property PACKAGE_PIN A8 [get_ports sw_pads]
set_property PACKAGE_PIN A10 [get_ports sw_reset]
set_property IOSTANDARD LVCMOS33 [get_ports {btn[*] sw_pads sw_reset}]

# TIMING DISPOSITION OF THE SIX HUMAN INPUTS (#449). Each is a mechanical
# contact with no clock relation to the FPGA; it changes at human speed and
# bounces for milliseconds. Each enters exactly one two-flop synchroniser
# (ASYNC_REG in the RTL): btn[i] and sw_pads -> u_pads/btn_q0, btn_q1, sel_q0,
# sel_q1, and sw_reset -> swr_q[1:0], all on the 12.288 MHz core clock (the SW3
# detector is on the core clock so its reset request is a same-clock async
# clear, not a clk100 -> core_clk recovery arc). Downstream of stage 2 everything is ordinary synchronous logic
# and stays fully timed -- the debouncer's DB_CYCLES (5 ms) lockout, then the
# sequencer. So the ONLY untimed arc is port -> stage-1 D, and that arc has no
# meaningful setup/hold requirement: a metastable stage-1 resolves within a
# core period, and a sample one clock early or late moves a press by 81 ns in a
# press-to-sound latency of 14.7 ms (pads_rom.LAT). These are false paths FROM
# THE PORT, as btn_reset is in the release XDC; fpga/build_arty_pads.py asserts
# on the routed design that each port's only timing endpoint is its stage-1
# flop, that stage 1 feeds only stage 2, both carry ASYNC_REG, and every path
# from the port is a False Path (PADS_INPUT_REFUSED otherwise, exit 3).
set_false_path -from [get_ports {btn[0]}]
set_false_path -from [get_ports {btn[1]}]
set_false_path -from [get_ports {btn[2]}]
set_false_path -from [get_ports {btn[3]}]
set_false_path -from [get_ports sw_pads]
set_false_path -from [get_ports sw_reset]

set_property PACKAGE_PIN H5 [get_ports {led[0]}]
set_property PACKAGE_PIN J5 [get_ports {led[1]}]
set_property PACKAGE_PIN T9 [get_ports {led[2]}]
set_property PACKAGE_PIN T10 [get_ports {led[3]}]
set_property IOSTANDARD LVCMOS33 [get_ports {led[*]}]

# JA laid out for a PCM5102 breakout plugged STRAIGHT into JA's top row, the
# same layout as fpga/boards/arty-a7-100-sd.xdc (#408). The breakout's header,
# read from its VIN end, is VIN GND LCK DIN BCK SCK; plugged into JA's top row
# (VCC and GND at positions 6 and 5) that is JA6..JA1, so:
#
#   JA1 G13 dac_sck (constant 0)   JA2 B11 BCK   JA3 A11 DIN   JA4 D12 LCK
#   JA5 GND                        JA6 3.3 V
#
# NOT the shared XDC's jumper layout (JA1/2/3 -> BCLK/WSEL/DIN), which leaves
# JA4 unassigned and so delivers NO word clock to a directly-plugged breakout.
# That produces total silence while every digital check passes: on 2026-09-28
# R0 was flashed onto this bench and made no sound while held_note_audible.py
# reported a decoded I2S peak of 12,760 LSB on the same bytes. Since #449's
# acceptance includes a by-ear check, the layout has to match the bench.
#
# This file is standalone (it assigns every port once) rather than an override
# read after the shared XDC, so #408's careful port-move ordering is not needed
# here: no port is ever reassigned off a site another port already holds.
set_property PACKAGE_PIN B11 [get_ports i2s_bclk]
set_property PACKAGE_PIN D12 [get_ports i2s_lrclk]
set_property PACKAGE_PIN A11 [get_ports i2s_sdata]
set_property PACKAGE_PIN G13 [get_ports dac_sck]
set_property IOSTANDARD LVCMOS33 [get_ports {i2s_bclk i2s_lrclk i2s_sdata dac_sck}]
set_property DRIVE 4 [get_ports {i2s_bclk i2s_lrclk i2s_sdata dac_sck}]
set_property SLEW SLOW [get_ports {i2s_bclk i2s_lrclk i2s_sdata dac_sck}]

# JB1/2/3/4 -> external controller SCK/MOSI/MISO/CS_N, all 3.3 V.
set_property PACKAGE_PIN E15 [get_ports spi_sck]
set_property PACKAGE_PIN E16 [get_ports spi_mosi]
set_property PACKAGE_PIN D15 [get_ports spi_miso]
set_property PACKAGE_PIN C15 [get_ports spi_cs_n]
set_property IOSTANDARD LVCMOS33 [get_ports {spi_sck spi_mosi spi_miso spi_cs_n}]
set_property PULLUP TRUE [get_ports spi_cs_n]
set_property PULLDOWN TRUE [get_ports {spi_sck spi_mosi}]

# USB-UART (FTDI): A9 = uart_rxd, D10 = uart_txd. In pads mode uart_rxd is
# deselected by pads_seq's source mux (core_rxd = eff ? sender : uart_rxd); the
# mux is combinational ahead of the bridge's stage-1 flop, so the false path
# below still names the one endpoint uart_rxd reaches.
set_property PACKAGE_PIN A9 [get_ports uart_rxd]
set_property PACKAGE_PIN D10 [get_ports uart_txd]
set_property IOSTANDARD LVCMOS33 [get_ports {uart_rxd uart_txd}]
set_property PULLUP TRUE [get_ports uart_rxd]

set_property ASYNC_REG TRUE [get_cells -hier -regexp {.*u_spi/(sck_q|mosi_q|csn_q)_reg\[[01]\]}]
set_property ASYNC_REG TRUE [get_cells -hier -regexp {.*g_uart\.u_uart/rx_q_reg\[[01]\]}]
set_false_path -from [get_ports spi_sck] -to [get_pins -hier -regexp {.*u_spi/sck_q_reg\[0\]/D}]
set_false_path -from [get_ports spi_mosi] -to [get_pins -hier -regexp {.*u_spi/mosi_q_reg\[0\]/D}]
set_false_path -from [get_ports spi_cs_n] -to [get_pins -hier -regexp {.*u_spi/csn_q_reg\[0\]/D}]
set_false_path -from [get_ports uart_rxd] -to [get_pins -hier -regexp {.*g_uart\.u_uart/rx_q_reg\[0\]/D}]

set_property CONFIG_VOLTAGE 3.3 [current_design]
set_property CFGBVS VCCO [current_design]

# ---- outputs: the release XDC's budgets, unchanged (reasons there) ------------
create_generated_clock -name i2s_bclk_ext \
    -source [get_pins hardware_clock.mmcm/CLKOUT0] -divide_by 4 [get_ports i2s_bclk]
set_output_delay -clock i2s_bclk_ext -max 8.200 [get_ports i2s_sdata]
set_output_delay -clock i2s_bclk_ext -min 154.560 [get_ports i2s_sdata]
set_output_delay -clock i2s_bclk_ext -max 8.200 [get_ports i2s_lrclk]
set_output_delay -clock i2s_bclk_ext -min 154.560 [get_ports i2s_lrclk]
set_output_delay -clock hardware_clock.clock_raw -max 54.958 [get_ports spi_miso]
set_output_delay -clock hardware_clock.clock_raw -min 0.000 [get_ports spi_miso]
# led[1] core out of reset, led[2] pads ready, led[3] a press was accepted:
# indicators with no receiver, budgeted at the full core period as in the
# release XDC. led[0] is the MMCM's LOCKED, as there.
set_output_delay -clock hardware_clock.clock_raw -max 0.000 [get_ports {led[1] led[2] led[3]}]
set_output_delay -clock hardware_clock.clock_raw -min 0.000 [get_ports {led[1] led[2] led[3]}]
set_output_delay -clock hardware_clock.clock_raw -max 0.000 [get_ports uart_txd]
set_output_delay -clock hardware_clock.clock_raw -min 0.000 [get_ports uart_txd]
