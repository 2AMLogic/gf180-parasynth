# Arty A7-100 Rev D/E; Digilent master XDC 00a3404901f35aa9567b01ecb3f2c233b6efe9f4.
# https://github.com/Digilent/digilent-xdc/blob/00a3404901f35aa9567b01ecb3f2c233b6efe9f4/Arty-A7-100-Master.xdc
set_property PACKAGE_PIN E3 [get_ports clk_100mhz]
set_property IOSTANDARD LVCMOS33 [get_ports clk_100mhz]
create_clock -name clk100 -period 10.000 [get_ports clk_100mhz]
set_property PACKAGE_PIN D9 [get_ports btn_reset]
set_property IOSTANDARD LVCMOS33 [get_ports btn_reset]
set_property PACKAGE_PIN H5 [get_ports {led[0]}]
set_property PACKAGE_PIN J5 [get_ports {led[1]}]
set_property PACKAGE_PIN T9 [get_ports {led[2]}]
set_property PACKAGE_PIN T10 [get_ports {led[3]}]
set_property IOSTANDARD LVCMOS33 [get_ports {led[*]}]

# JA1/2/3 -> PCM5102 BCLK/WSEL/DIN. JA5 or JA11 -> GND; JA6 or JA12 -> VIN.
set_property PACKAGE_PIN G13 [get_ports i2s_bclk]
set_property PACKAGE_PIN B11 [get_ports i2s_lrclk]
set_property PACKAGE_PIN A11 [get_ports i2s_sdata]
set_property IOSTANDARD LVCMOS33 [get_ports {i2s_bclk i2s_lrclk i2s_sdata}]
set_property DRIVE 4 [get_ports {i2s_bclk i2s_lrclk i2s_sdata}]
set_property SLEW SLOW [get_ports {i2s_bclk i2s_lrclk i2s_sdata}]

# JB1/2/3/4 -> external controller SCK/MOSI/MISO/CS_N, all 3.3 V.
set_property PACKAGE_PIN E15 [get_ports spi_sck]
set_property PACKAGE_PIN E16 [get_ports spi_mosi]
set_property PACKAGE_PIN D15 [get_ports spi_miso]
set_property PACKAGE_PIN C15 [get_ports spi_cs_n]
set_property IOSTANDARD LVCMOS33 [get_ports {spi_sck spi_mosi spi_miso spi_cs_n}]
set_property PULLUP TRUE [get_ports spi_cs_n]
set_property PULLDOWN TRUE [get_ports {spi_sck spi_mosi}]

# The link samples asynchronous inputs through two flops. Only the paths to
# the first stages are asynchronous; do not exempt the engine's timing.
set_property ASYNC_REG TRUE [get_cells -hier -regexp {.*u_spi/(sck_q|mosi_q|csn_q)_reg\[[01]\]}]
set_false_path -from [get_ports spi_sck] -to [get_pins -hier -regexp {.*u_spi/sck_q_reg\[0\]/D}]
set_false_path -from [get_ports spi_mosi] -to [get_pins -hier -regexp {.*u_spi/mosi_q_reg\[0\]/D}]
set_false_path -from [get_ports spi_cs_n] -to [get_pins -hier -regexp {.*u_spi/csn_q_reg\[0\]/D}]
set_false_path -from [get_ports btn_reset]

# The output budgets below close the external timing; the core WNS alone
# still says nothing about pin timing.
set_property CONFIG_VOLTAGE 3.3 [current_design]
set_property CFGBVS VCCO [current_design]

# ---------------------------------------------------------------------------
# External I/O timing -- closes the seven unconstrained outputs. The budgets,
# their citations and the recorded assumptions live in fpga/ext_io_timing.py;
# the per-output disposition is in fpga/ARTY.md. No false paths on functional
# outputs.
#
# i2s_bclk is a FORWARDED CLOCK: BCLK is bit 1 of the core-clock frame cycle
# counter (rtl-sketch/i2s_tx.v:41) = 12.288 MHz / 4 = 3.072 MHz, and the DAC
# (Adafruit #6250, TI PCM5102) is an I2S slave clocked by it. Declaring the
# port as a generated clock makes SDATA and LRCLK source-synchronous data
# against the clock that actually arrives at the receiver, skew included.
create_generated_clock -name i2s_bclk_ext \
    -source [get_pins hardware_clock.mmcm/CLKOUT0] -divide_by 4 [get_ports i2s_bclk]

# PCM5102 (SLOS811 / SLAS764B, Table 7 "Audio Interface Slave Timing"):
# tDS = tDH = 8 ns (DIN vs BCLK rising), tLB = tBL = 8 ns (LRCLK vs BCLK
# rising). SDATA and LRCLK change on BCLK's falling edge (i2s_tx.v:48), half
# a BCLK period before the sampling edge. Jumpers are short (~0.2 ns) and of
# similar length; 0.2 ns is added to both budgets for the residual
# data-minus-clock flight imbalance (recorded assumption, not a measurement).
set_output_delay -clock i2s_bclk_ext -max 8.200 [get_ports i2s_sdata]
set_output_delay -clock i2s_bclk_ext -min -8.200 [get_ports i2s_sdata]
set_output_delay -clock i2s_bclk_ext -max 8.200 [get_ports i2s_lrclk]
set_output_delay -clock i2s_bclk_ext -min -8.200 [get_ports i2s_lrclk]
# BCLK's own receiver requirements (tBCY >= 40 ns, tBCH/tBCL >= 16 ns, fBCK <=
# 24.576 MHz) are met by construction at 3.072 MHz / 50 % duty and are checked
# arithmetically in fpga/ext_io_timing.py::bclk_checks. The generated clock
# above is this port's constraint; a forwarded clock carries no output delay.

# spi_miso: the FPGA is the SPI SLAVE (DR 0007 rev 2; mode 0, MSB first,
# 48-bit frames; controller SCK <= 2.0 MHz for writes). MISO is NOT launched
# by SCK: spi_ctl.v re-drives it in the core-clock domain through the same
# two-flop synchroniser that samples SCK (spi_ctl.v:124-141), so a bit leaves
# the FPGA 3-4 core clocks (244.1-325.5 ns) after the SCK falling edge that
# shifted it, plus clock-to-out. A mode-0 controller samples on the SCK rising
# edge, T_sck/2 later, so the brief's generic virtual-SCK output delay would
# be analyzed against an arbitrary core-to-SCK edge alignment and fail
# irrespective of real margins; the STA-expressible part is a clock-to-out
# bound from the launch register:
#     T_sck/2 >= 4*T_core + CO + flight + t_su(controller)
# At the 2.0 MHz write ceiling (half period 250.0 ns < 4*T_core = 325.5 ns)
# this is UNSATISFIABLE for any CO >= 0: status readback is NOT qualified at
# the write rate. Qualified readback rate: 1.4 MHz --
#     357.143 >= 325.521 + CO + 0.2 (flight, assumed) + 5.0 (controller setup,
#     ASSUMED: no guaranteed spec for the external controller exists)
# => CO <= 26.422 ns, enforced below against the core clock (period 81.380 ns:
# the setup check becomes CO <= 81.380 - 54.958).
set_output_delay -clock hardware_clock.clock_raw -max 54.958 -min 0.000 [get_ports spi_miso]

# led[1..3] are indicators with no synchronous receiver: LED1 reset release,
# LED2 heartbeat, LED3 LRCLK (fpga/ARTY.md wiring table). No receiver-derived
# budget exists to quote, so the budget is the full core period -- any
# clock-to-out below 81.38 ns is invisible on a human timescale -- applied as
# a real, checkable output delay so the endpoints stay counted and the
# internal STA of their drivers is unchanged. These are documented exceptions
# from receiver-derived budgeting, NOT false paths.
set_output_delay -clock hardware_clock.clock_raw -max 0.000 -min 0.000 \
    [get_ports {led[1] led[2] led[3]}]
