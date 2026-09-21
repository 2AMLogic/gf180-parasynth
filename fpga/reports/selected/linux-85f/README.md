# Linux 85F build result

State: **NO-VERDICT**. [Completed workflow](https://github.com/2AMLogic/gf180-parasynth/actions/runs/35666476657).

Configuration: `OSC2X=1 FILTER2X=1 PULSE2X=0`, LFE5U-85F / CABGA381. This is the selected baseline; it does not establish fit for the pulse 2× candidate in #192.

No qualified bitstream: core clock constraint is not the selected PLL rate

`publication.json` binds all Verilog, ROM and pin/clock constraint inputs, original artifacts and the preserved core simulation. `report.json`, `timing.json` when available, the pinned toolchain record and complete gzip-compressed logs preserve the raw evidence. The larger synthesis netlist/configuration remain in the linked CI artifact, with their hashes recorded here.

The reused complete-phrase SPI→I²S simulation covers the core; the FPGA wrapper and physical controller/DAC are outside that scope. **Physical live control and line-output capture are not verified.** The hardware connection and audio input have not been identified.

The publication gate started red; 13 publication/build/toolchain tests pass, including missing/wrong clock, missed timing, NaN, overfull-device and missing-path controls. The interrupted local route is retained separately as NO-VERDICT. No sound setting or tolerance changed.
