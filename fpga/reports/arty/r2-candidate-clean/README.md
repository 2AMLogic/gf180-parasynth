# Arty wrapper UART-bridge clean run, R2 candidate (PULSE2X=1)

This is the wrapper's digital proof for the **R2 candidate** tree (`fpga/release/R2.md`):
`OSC2X=1 FILTER2X=1 PULSE2X=1`, the S_WIN skip in `voice_dp.v`, and the rectangle
headroom in `polyblep_saw_pair.v`. `fpga/build_arty.py --image r2-candidate` requires it
(`IMAGE_VERIFICATION["r2-candidate"]`). R1's record, `rev14-clean`, is untouched and
still binds R1.

It was run on the build box at `70e1ea8` through `tools/run_all.py` (`run_all.json`:
2 jobs, exit 0):

    python3 fpga/verify_uart_bridge.py --pulse2x --scenario phrase --outdir build/uart-r2
    python3 fpga/verify_uart_bridge.py --pulse2x --start-red --outdir build/uart-r2-startred

| file | what |
|---|---|
| `verification.json` / `.txt` | clean `phrase`, configuration `{"OSC2X": 1, "FILTER2X": 1, "PULSE2X": 1}`: 87/87 writes, 6,734 I2S periods bit-exact against the PULSE2X=1 model, `worst_strobe_cycle` **171** of 256 (R1's record: 176) |
| `start-red.json` / `.txt` | the same bench, in the same configuration, against `fpga/stubs/arty_a7_uart_stub.v`: `FAIL`, 0 of 26 writes |

**The builder precondition** (`build_arty.py --image r2-candidate --prepare-only`, box)
is met. The prepared `synth_design` line carries `-verilog_define VOICE_PULSE_2X`.

**The control:** the default R1 configuration, handed this record, is refused
("verification must be a clean pass of the selected configuration").

**Scope.** The phrase is a sawtooth voice phrase, the same stimulus as R1's
record. It proves the wrapper and the unchanged paths on R2's sources, including
the S_WIN change on the saw path. It does not exercise rectangles. R2's rectangle
path at the UART pins is covered by
`verify_deadline --scenario arty-uart --pulse2x`, which has a pulse29 section
(3300/3300 periods exact; its headroom control is caught), and at the SPI pins by
the M5A/M5B phrases (`fpga/reports/r2-candidate/`).

There is no Vivado build, no bitstream and no board.
