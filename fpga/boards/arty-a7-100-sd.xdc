# Arty A7-100T no-DAC demo output (#406), read AFTER arty-a7-100.xdc by
# fpga/build_arty_sd.py for fpga/rtl/arty_a7_sd_top.v only. The shared XDC is
# not edited: its text is bound by hash to published evidence.
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
