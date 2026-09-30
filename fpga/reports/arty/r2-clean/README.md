# Arty wrapper UART-bridge clean run, R2 (PULSE2X=1)

This is the wrapper's digital proof for **R2** (`fpga/release/R2.md`):
`OSC2X=1 FILTER2X=1 PULSE2X=1`, the S_WIN skip in `voice_dp.v`, the rectangle
headroom in `polyblep_saw_pair.v` and the #354 26-bit `xg` in `ladder_dp_n.v`.
`fpga/build_arty.py --image r2` requires it (`IMAGE_VERIFICATION["r2"]`), and
`fpga/publish_arty.py` binds it for an `r2` build. R1's record, `rev14-clean`,
is untouched and still binds R1.

It was re-run on the build box at `9703817` (the settled set, #364 folded in)
through `tools/run_all.py`, as part of R2's evidence batch
(`fpga/reports/r2-candidate/`):

    python3 fpga/verify_uart_bridge.py --pulse2x --scenario phrase --outdir build/r2s/uart-r2
    python3 fpga/verify_uart_bridge.py --pulse2x --start-red --outdir build/r2s/uart-r2-startred

| file | what |
|---|---|
| `verification.json` / `.txt` | clean `phrase`, configuration `{"OSC2X": 1, "FILTER2X": 1, "PULSE2X": 1}`: 87/87 writes, 6,734 I2S periods bit-exact against the PULSE2X=1 model, `worst_strobe_cycle` **171** of 256 (R1's record: 176) |
| `start-red.json` / `.txt` | the same bench, in the same configuration, against `fpga/stubs/arty_a7_uart_stub.v`: `FAIL`, 0 of 26 writes |

Against the previous record (at `70e1ea8`) only `ladder_dp_n.v`'s digest moved.

**Scope.** The phrase is a sawtooth voice phrase, the same stimulus as R1's
record. It proves the wrapper and the unchanged paths on R2's sources. It does
not exercise rectangles; R2's rectangle path at the UART pins is covered by
`verify_deadline --scenario arty-uart --pulse2x` (pulse29 section) and by
T-PLAY-DIGITAL `r2` (`held-m5a-pulse`), and at the SPI pins by the M5A/M5B phrases.
