# Arty wrapper UART-bridge clean run, clap-L2 tree (contract revision 13)

The wrapper's digital proof for the tree that carries the clap's final strike
(contract revision 13: `ENV_FRATE` in `drum_regs.v`/`drum_dp.v`/`drum_kit.v`,
wired through `synth_top.v`). This is what `fpga/publish_arty.py`'s
`VERIFICATION_BY_WRAPPER["arty_a7_top"]` binds and what
`fpga/build_arty.py --prepare-only` validates against the live source set.
Run on the build box at commit `3886191` (branch `b1/l2-integrate`, clean tree),
through `tools/run_all.py` (both jobs exit 0; `docs/scorecard/clap-l2/headroom/batch1.*`):

    python3 fpga/verify_uart_bridge.py --scenario phrase --outdir build/uart-l2
    python3 fpga/verify_uart_bridge.py --start-red --outdir build/uart-startred

| file | what |
|---|---|
| `verification.json` | the clean `phrase` run: 87/87 writes delivered, 6,734 I2S periods bit-exact against the integer model, `worst_strobe_cycle` 176 of 256 |
| `verification.txt` | that run's transcript, hashed into the record as `transcript_sha256` |
| `start-red.json` | the same bench against `fpga/stubs/arty_a7_uart_stub.v`: `FAIL`, 0 of 26 writes delivered |
| `start-red.txt` | that run's transcript |

## Why a new directory

`drift-clean` is the proof of the pre-L2 tree and `uart-clean` the proof the
**published R0 bitstream** (`a66c9349…`, checkpoint `6c3c22c5…`) cites by hash.
Evidence directories are added, never rewritten.
`tools/test_check_arty_evidence_binding.py::test_r0_published_image_is_bound_to_its_historical_source_set`
pins R0 to `uart-clean` and asserts the live tree is NOT R0's source set.

## What L2 changed here, measured

Against `drift-clean`, exactly four source hashes move (`synth_top.v`,
`drum_regs.v`, `drum_kit.v`, `drum_dp.v`) and **no measured field moves**: the
comparison block and the transcript hash (`4fbbbb87…`) are identical. That is
expected and is also this record's limit: the `phrase` scenario is a voice
phrase and does not fire the clap, so it proves that the wrapper, link and
unchanged paths still work around the new drum RTL, **not** that the final
strike plays at the pins. That evidence is the SPI production-path clap phrase
in `docs/scorecard/clap-l2/` (30,201 of 30,201 I2S periods identical, four
RTL controls caught at the pins).

## What this record does NOT cover

No Vivado, no bitstream, no board. Icarus Verilog at the wrapper pins with the
MMCM bypassed; no physical timing claim. No image of this tree exists (R1 is
plan087 Milestone C).
