create_generated_clock -name i2s_bclk_imp -source [get_pins hardware_clock.mmcm/CLKOUT0] -divide_by 4 [get_ports i2s_bclk]
set_output_delay -clock i2s_bclk_imp -max 200.000 [get_ports {i2s_sdata i2s_lrclk}]
set_output_delay -clock hardware_clock.clock_raw -max 81.000 [get_ports spi_miso]
