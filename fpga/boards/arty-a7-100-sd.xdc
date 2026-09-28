# Arty A7-100T demo image (#406), read AFTER arty-a7-100.xdc by
# fpga/build_arty_sd.py for fpga/rtl/arty_a7_sd_top.v only. The shared XDC is
# not edited: its text is bound by hash to published evidence. Pins below are
# from Digilent's Arty-A7-100 master XDC at 00a3404 (checked 2026-09-28).
#
# Pmod JD (200 ohm series resistors on the board). Pins from Digilent's
# Arty-A7-100 master XDC: jd[0] = D4 (JD1), jd[1] = D3 (JD2).
set_property PACKAGE_PIN D4 [get_ports sd_left]
set_property PACKAGE_PIN D3 [get_ports sd_right]
set_property IOSTANDARD LVCMOS33 [get_ports {sd_left sd_right}]
set_property DRIVE 8 [get_ports {sd_left sd_right}]
set_property SLEW SLOW [get_ports {sd_left sd_right}]
# External-I/O disposition: a one-bit PDM stream into an RC low-pass has no
# receiving clock, so there is no setup or hold to budget and no output delay
# to state. What matters is edge timing jitter (the clock's, not the route's)
# and rise/fall symmetry of the driver, neither of which an output delay
# expresses. An explicit exception, not a token delay.
# One line per port: report_exceptions prints `[get_ports x]` for a single
# port (seen in every published build); a braced list's form is unseen, and
# xdc_bindings.check_route compares the two texts.
set_false_path -to [get_ports sd_left]
set_false_path -to [get_ports sd_right]

# ---- JA for a PCM5102 board plugged straight in -----------------------------
# The breakout's header, from its VIN end: VIN GND LCK DIN BCK SCK. Plugged
# into JA's top row (VCC GND at pins 6/5) that is JA6..JA1, so:
#   JA1 G13 dac_sck (0)   JA2 B11 BCK   JA3 A11 DIN   JA4 D12 LCK
# These OVERRIDE the shared XDC's JA1..JA3 map. The order matters: each port
# moves onto a site that is free at that moment (LRCLK leaves B11 for the
# unused D12, then BCLK takes B11, then dac_sck takes the freed G13), so no
# two ports ever share a site. fpga/test_arty_sd_top.py replays this order.
set_property PACKAGE_PIN D12 [get_ports i2s_lrclk]
set_property PACKAGE_PIN B11 [get_ports i2s_bclk]
set_property PACKAGE_PIN G13 [get_ports dac_sck]
set_property IOSTANDARD LVCMOS33 [get_ports dac_sck]
set_property DRIVE 4 [get_ports dac_sck]
set_property SLEW SLOW [get_ports dac_sck]
# dac_sck is a constant 0: no transition, so no timing to budget.
set_false_path -to [get_ports dac_sck]
