# Published 85F baseline

**BUILT — TIMING PASS.** The existing [Linux build](https://github.com/2AMLogic/gf180-parasynth/actions/runs/35666476657) at `11217322f588b80d890b5dc7b9444e8d20c48c3f` supplies this bitstream. No synthesis or routing was repeated for publication.

| Measure | Result |
| --- | ---: |
| Core frequency achieved | 12.886930 MHz |
| Reported clock constraint | 12.288183 MHz |
| Physical PLL design frequency | 725/59 = 12.288136 MHz |
| Logic cells | 30,806 / 83,640 |
| Flip-flops | 14,008 / 83,640 |
| Multiplier blocks | 104 / 156 |

Configuration is **OSC2X=1 FILTER2X=1 PULSE2X=0**. This is historical baseline evidence, not a build of the current checkout and not fit evidence for #192 pulse 2×. `publication.json` records the original source identity and the source differences in today's checkout. The reused complete-phrase SPI→I²S core simulation is bound by hashes; FPGA wrapper and physical playback remain outside that evidence.

Bitstream: [ecp5.bit](ecp5.bit), SHA-256 `e15314dc81ce61e88a5da5a56c072927bc967d82d7d3d4fb57c18738fd69f096`.

## Publication repair

Pinned nextpnr [arch.h:976](https://github.com/YosysHQ/nextpnr/blob/3e53a0bf44d13c0de603dd089a323ea85d67d4ef/ecp5/arch.h#L976) truncates delay to integer picoseconds; [pack.cc:2852](https://github.com/YosysHQ/nextpnr/blob/3e53a0bf44d13c0de603dd089a323ea85d67d4ef/ecp5/pack.cc#L2852) uses it for derived periods. The ideal 81,379.310345 ps becomes 81,379 ps, with 0.005 ps more float32 conversion error in the JSON frequency. The publisher accepts the exact design period or that single quantized period, with 0.01 ps float conversion tolerance. Timing must still meet the reported constraint. Nearby incorrect frequencies, including nominal 12.288 MHz, are rejected.

The committed real `timing.json` failed before the repair and passes afterward. Nineteen publication/build/toolchain tests pass. **Wrong-then-right: one publication-gate correction, zero changes to routed artifacts.** The prior rejection is preserved in `publication-before-clock-fix.json`.

Reproduce publication from the original downloaded CI artifact into an empty directory:

```sh
python3 fpga/publish_selected.py /path/to/selected-85f --historical --workflow-url https://github.com/2AMLogic/gf180-parasynth/actions/runs/35666476657 --out /tmp/reproduced-85f
python3 -m pytest -q fpga/test_publish_selected.py fpga/test_build_selected.py tools/test_setup_ci_oss_cad.py
```

The builder requires source agreement with its simulation and refuses changed sources; rebuilding this exact baseline requires checkout `11217322`. Routing is now manually dispatched, so publishing artifacts does not rerun it. **Physical SPI playback, external DAC output and line-input capture remain unverified.**
