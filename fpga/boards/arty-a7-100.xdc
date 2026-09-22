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

# External DAC and controller timing are still unqualified. Do not suppress
# missing output-delay warnings or claim pin timing from the core WNS.
set_property CONFIG_VOLTAGE 3.3 [current_design]
set_property CFGBVS VCCO [current_design]
