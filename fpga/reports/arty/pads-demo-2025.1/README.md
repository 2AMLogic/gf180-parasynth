# Arty pads demo build record (#449)

`arty_a7_pads_top` on `xc7a100tcsg324-1`, Vivado 2025.1, built 2026-09-29 on the
repo's pinned AWS box (`m7a.2xlarge`, AMD Vivado ML 2025.1 Developer AMI,
`ami-0937df144db16a172`). Wall time 8m26s.

**This is a build record, not a publication.** The pads image is never a
release image: `publish_arty` has no `VERIFICATION_BY_WRAPPER` entry for it.

| | |
|---|---|
| `arty_pads.bit` | `c27bde5d33e397ed09a80c41414e9c59393596d4fdb749cb230a46579d71d895` |
| state | `PASS` |
| top / part | `arty_a7_pads_top` / `xc7a100tcsg324-1` |
| configuration | OSC2X=1 FILTER2X=1 PULSE2X=0 |
| kit | `321a93546cfa5ffa…` (R1's, re-derived by `pads_rom.check_rom`) |

## Timing, from the routed design

| | WNS | WHS | WPWS |
|---|---|---|---|
| slack (ns) | **+15.82** | **+0.03** | **+3.0** |
| failing endpoints | 0 / 44078 | 0 / 44078 | 0 / 14098 |

Zero failing endpoints in all three classes, so the verdict is PASS.

## The human inputs

`pads_inputs.rpt` is the gate that matters for a demo driven by hand: each of
`btn[0..3]`, `sw_pads` and `sw_reset` must reach exactly one flop, that flop
feed exactly one flop, both carry `ASYNC_REG`, and every timing path from the
port be a False Path. All six pass; the report's last line is `END	0`.

## JA is the direct-plug layout

`fpga/boards/arty-a7-100-pads.xdc` follows `arty-a7-100-sd.xdc` (#408):
`JA1 dac_sck` held at 0, `JA2 BCK`, `JA3 DIN`, `JA4 LRCLK`. That matches a
PCM5102 breakout plugged straight into JA's top row. The shared XDC's jumper
layout leaves JA4 unassigned and delivers no word clock, which is silence with
every digital check still passing -- measured on this bench on 2026-09-28.
The breakout's own XSMT must be tied high and FMT low (#460).

## Scrubbing

`.rpt`, `.json` and `.tcl` files here have their `| Host :` line removed and the
build box's home path replaced with `<box>`, following the same convention as
`sd-demo-2025.1`. `arty_pads.bit` is untouched. Hashes before and after:

| File | As Vivado wrote it | As committed |
|---|---|---|
| `build.tcl` | `d253d56238435294…` | `45b577ed0d63c6db…` |
| `clocks.rpt` | `29c2f4ec08306d70…` | `7599b23e6b2f7e22…` |
| `constraint_matches.rpt` | `1ea966dae73eb829…` | `1ea966dae73eb829…` |
| `drc.rpt` | `997ebc9c12247791…` | `0aa03d772134d0ea…` |
| `exceptions.rpt` | `69777871e8d279f4…` | `f3c4799bedeb4f73…` |
| `pads_inputs.rpt` | `25203a319c46a707…` | `25203a319c46a707…` |
| `report.json` | `f2d3ebcd308af93a…` | `9434f5c0eddc7b2d…` |
| `timing.rpt` | `ba07c77fce142983…` | `ab2cfd7c01994d8a…` |
| `utilization.rpt` | `1149950ac9518360…` | `0586d49c14cc5ac1…` |

## Not shown here

Physical playback. The image was flashed to the bench board on 2026-09-29 and
the by-ear check of #449 is the operator's, recorded on the issue rather than
in this directory. `hardware_playback_tested` in `report.json` stays false
until that is recorded.
